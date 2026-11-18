"""FinCrime Agent microservice application."""
from contextlib import asynccontextmanager
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from card_chat.config import settings
from card_chat.provider import make_model
from card_chat.agent import FinCrimeAgent
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.security import protect

logger = logging.getLogger(__name__)


class TurnRequest(BaseModel):
    history: list[dict[str, Any]] = []
    collected: dict[str, Any] = {}
    allowed_tools: dict[str, Any] = {}


class RemoteFinCrimeAgent:
    """Client for delegating to a remote FinCrime Agent microservice over HTTP."""

    def __init__(self, base_url: str, fallback_agent=None, allow_fallback: bool = False, headers_fn=None):
        self.base_url = base_url.rstrip("/")
        self.fallback_agent = fallback_agent
        self.allow_fallback = allow_fallback
        self.headers_fn = headers_fn
        self.name = "fincrime_agent"

    async def interpret_turn(self, history: list, collected: dict, allowed_tools: dict):
        import httpx
        import inspect
        payload = {
            "history": history or [],
            "collected": collected or {},
            "allowed_tools": allowed_tools or {}
        }
        headers = {}
        if self.headers_fn:
            try:
                if inspect.iscoroutinefunction(self.headers_fn):
                    headers = await self.headers_fn()
                else:
                    headers = self.headers_fn()
            except Exception as e:
                logger.error("Failed to generate service headers for RemoteFinCrimeAgent: %s", e)
        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.post(f"{self.base_url}/turn", json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    kind = data.get("kind")
                    if kind == "reply":
                        return CustomerReply(data.get("text", ""))
                    elif kind == "proposal":
                        return ProposedToolCall(data.get("name"), data.get("arguments", {}))
                logger.error("Remote FinCrime Agent returned HTTP %s: %s", resp.status_code, resp.text)
                return CustomerReply("The financial crime service returned an unexpected response. Please try again.")
        except Exception as e:
            logger.error("Remote FinCrime Agent call failed: %s", e)
            if self.allow_fallback and self.fallback_agent:
                logger.warning("Explicitly permitted fallback to in-process FinCrime agent activated")
                return await self.fallback_agent.interpret_turn(history, collected, allowed_tools)
            return CustomerReply("I'm having trouble connecting to the financial crime service. Please try again later.")


def create_app(model=None, *, config=None):
    config = settings() if config is None else config

    @asynccontextmanager
    async def lifespan(app):
        app.state.model = model or make_model(config)
        app.state.agent = FinCrimeAgent(app.state.model)
        try:
            yield
        finally:
            await app.state.model.close()

    app = FastAPI(title="FinCrime Agent Service", version="1.0", lifespan=lifespan)
    if config.get("OIDC_ISSUER") and config.get("ALLOW_INSECURE_LOCAL") != "1":
        protect(app, config, ["agent", "mcp"], ["agent:invoke", "tools:execute"], "agent-service", customer=False)
    from card_chat.observability import instrument
    instrument(app, config, 'fincrime-agent')

    @app.get("/health")
    @app.get("/health/ready")
    async def health():
        return {"status": "ok", "service": "fincrime-agent"}

    @app.post("/turn")
    async def turn(req: TurnRequest):
        agent = getattr(app.state, "agent", None)
        if not agent:
            raise HTTPException(503, "Agent not initialized")
        turn_result = await agent.interpret_turn(req.history, req.collected, req.allowed_tools)
        if isinstance(turn_result, CustomerReply):
            return {"kind": "reply", "text": turn_result.text}
        elif isinstance(turn_result, ProposedToolCall):
            return {"kind": "proposal", "name": turn_result.name, "arguments": turn_result.arguments}
        raise HTTPException(500, "Unexpected turn result")

    return app


app = create_app()
