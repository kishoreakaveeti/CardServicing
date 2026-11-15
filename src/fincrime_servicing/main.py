"""FastAPI application for FinCrime Servicing API."""
from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from card_chat.config import settings
from card_chat.security import protect, principal
from fincrime_servicing.models import CardBlockRequest, CardBlockResult, AccountStatusResult
from fincrime_servicing.persistence import SQLiteFinCrimeStore
from fincrime_servicing.service import FinCrimeService

logger = logging.getLogger(__name__)


def create_app(fincrime_store=None, service=None, *, config=None):
    config = settings() if config is None else config
    db_path = config.get("FINCRIME_DB", str(Path("data") / "local" / "fincrime.db"))
    store = fincrime_store or SQLiteFinCrimeStore(db_path)
    svc = service or FinCrimeService(
        store=store,
        cards_api_url=config.get("CARD_API_BASE_URL", "http://127.0.0.1:8010")
    )

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        app.state.fincrime = svc
        app.state.fincrime_store = store
        yield

    app = FastAPI(title="FinCrime Servicing API", version="1.0", lifespan=lifespan)
    protect(app, config, "fincrime", "tools:execute", "mcp-service", customer=True)
    from card_chat.observability import instrument
    instrument(app, config, "fincrime")

    def customer():
        value = principal.get()
        if not value:
            raise HTTPException(401, "Sign in required")
        return value

    @app.get("/health")
    @app.get("/health/ready")
    async def health():
        return {"status": "ok"}

    @app.post("/fincrime/cards/{card_id}/block", operation_id="fincrime_block_card", response_model=CardBlockResult)
    async def block_card(
        card_id: str,
        body: CardBlockRequest,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128)
    ):
        cust = customer()
        result, replayed = await app.state.fincrime.block_card(
            customer=cust,
            card_id=card_id,
            reason=body.reason,
            key=idempotency_key
        )
        response.status_code = 200
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return result

    @app.post("/fincrime/cards/{card_id}/unblock", operation_id="fincrime_unblock_card", response_model=CardBlockResult)
    async def unblock_card(
        card_id: str,
        body: CardBlockRequest,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128)
    ):
        cust = customer()
        result, replayed = await app.state.fincrime.unblock_card(
            customer=cust,
            card_id=card_id,
            reason=body.reason,
            key=idempotency_key
        )
        response.status_code = 200
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return result

    @app.get("/fincrime/accounts/{account_number}/status", operation_id="fincrime_account_status", response_model=AccountStatusResult)
    async def account_status(account_number: str):
        customer()
        return app.state.fincrime.get_account_status(account_number)

    @app.get("/fincrime/cards/{card_id}", operation_id="fincrime_get_card", response_model=CardBlockResult)
    async def get_card(card_id: str):
        customer()
        card = app.state.fincrime.get_blocked_card(card_id)
        if not card:
            raise HTTPException(404, "Card not found in FinCrime records")
        return card

    return app


app = create_app()
