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

    def __init__(self, base_url: str, fallback_agent=None):
        self.base_url = base_url.rstrip("/")
        self.fallback_agent = fallback_agent
        self.name = "fincrime_agent"

    async def interpret_turn(self, history: list, collected: dict, allowed_tools: dict):
        import httpx
        payload = {
            "history": history or [],
            "collected": collected or {},
            "allowed_tools": allowed_tools or {}
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.post(f"{self.base_url}/turn", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    kind = data.get("kind")
                    if kind == "reply":
                        return CustomerReply(data.get("text", ""))
                    elif kind == "proposal":
                        return ProposedToolCall(data.get("name"), data.get("arguments", {}))
                else:
                    logger.warning("Remote FinCrime Agent returned HTTP %s: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.warning("Remote FinCrime Agent call failed: %s; falling back to in-process agent", e)

        if self.fallback_agent:
            return await self.fallback_agent.interpret_turn(history, collected, allowed_tools)
        return CustomerReply("I'm having trouble connecting to the financial crime service. Please try again.")


def create_app(model=None, *, config=None):
    config = settings() if config is None else config
    m = model or make_model(config)
    agent_inst = FinCrimeAgent(m)

    @asynccontextmanager
    async def lifespan(app):
        app.state.model = m
        app.state.agent = agent_inst
        yield

    app = FastAPI(title="FinCrime Agent Service", version="1.0", lifespan=lifespan)
    app.state.model = m
    app.state.agent = agent_inst
    if config.get("OIDC_ISSUER") and config.get("ALLOW_INSECURE_LOCAL") != "1":
        try:
            protect(app, config, "agent", "agent:invoke", "agent-service", customer=False)
        except Exception as e:
            logger.warning("Could not enable security protection on fincrime-agent: %s", e)

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
