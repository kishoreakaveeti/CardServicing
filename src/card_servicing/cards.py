from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from card_servicing.config import CardSettings
from card_servicing.models import Card, CardCreate
from card_servicing.ports import CardStorePort, StorageUnavailable
from card_servicing.idempotency import IdempotencyConflict


router = APIRouter(prefix="/cards", tags=["cards"])


def get_card_store(request: Request) -> CardStorePort:
    return request.app.state.card_store


def get_card_settings(request: Request) -> CardSettings:
    return request.app.state.card_settings


@router.post(
    "", operation_id='create_card', response_model=Card, status_code=status.HTTP_201_CREATED,
    summary="Originate a simulated card for an existing account",
    description="Creates a simulation-only card number. Account existence is assumed. "
                "The same Idempotency-Key and normalized body replay the original response.",
    responses={409: {"description": "Key reused with a different request"},
               503: {"description": "Storage unavailable; retry with the same key"}},
)
def create_card(
    request: Request,
    response: Response,
    instruction: CardCreate,
    idempotency_key: Annotated[str, Header(
        alias='Idempotency-Key', min_length=1, max_length=128,
        pattern=r'^[A-Za-z0-9._:-]+$',
        description='Client-generated key, e.g. a UUID. Reuse only for retries of the same card.',
    )],
    store: Annotated[CardStorePort, Depends(get_card_store)],
    settings: Annotated[CardSettings, Depends(get_card_settings)],
) -> Card:
    trace_id = request.state.trace_id
    telemetry = request.app.state.telemetry
    account_fields = telemetry.account_fields(instruction) if hasattr(telemetry, 'account_fields') else {}
    try:
        card, replayed = store.create_idempotent(instruction, idempotency_key)
    except IdempotencyConflict:
        telemetry.emit('log', 'card_idempotency_conflict', trace_id=trace_id,
                       level='WARNING', **account_fields)
        raise HTTPException(status_code=409, detail='Idempotency key already used for a different instruction') from None
    except StorageUnavailable as exc:
        telemetry.emit('log', 'card_storage_failed', trace_id=trace_id,
                       level='ERROR', dependency=store.dependency_name, error_type=type(exc).__name__, **account_fields)
        raise HTTPException(status_code=503, detail='Card storage unavailable') from None
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Idempotency-Replayed'] = str(replayed).lower()
    telemetry.emit('log', 'card_replayed' if replayed else 'card_created', trace_id=trace_id,
                   level='INFO', card_id=str(card.id), status=card.status, **account_fields)
    return card
