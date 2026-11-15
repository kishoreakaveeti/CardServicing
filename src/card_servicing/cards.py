from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from card_servicing.config import CardSettings
from card_servicing.models import Card, CardCreate, CardOrigination
from card_servicing.ports import CardStorePort, StorageUnavailable, AccountNotOwned
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
                "The same Idempotency-Key and normalized body replay the original response. "
                "Ordinary issuance returns an existing card. Additional cards require additional_card=true.",
    responses={200: {'model': Card, 'description': 'Existing card returned; no new card created'},
               409: {"description": "Key reused with a different request"},
               503: {"description": "Storage unavailable; retry with the same key"}},
)
def create_card(
    request: Request,
    response: Response,
    instruction: CardOrigination,
    idempotency_key: Annotated[str, Header(
        alias='Idempotency-Key', min_length=1, max_length=128,
        pattern=r'^[A-Za-z0-9._:-]+$',
        description='Client-generated key, e.g. a UUID. Reuse only for retries of the same card.',
    )],
    store: Annotated[CardStorePort, Depends(get_card_store)],
    settings: Annotated[CardSettings, Depends(get_card_settings)],
) -> Card:
    from card_chat.security import principal
    customer = principal.get()
    banking = getattr(request.app.state, 'banking', None)
    if customer and banking and (banking.registered(customer['sub']) or banking.managed_account(instruction.account_number)):
        if not banking.owns(customer['sub'], instruction.account_number):
            raise HTTPException(403, 'Account is not authorized for this customer')
    if request.app.state.security.enabled:
        if (not customer or instruction.account_number not in customer['accounts']
                or instruction.user_name != customer['username']):
            raise HTTPException(403, 'Account is not authorized for this customer')
    trace_id = request.state.trace_id
    telemetry = request.app.state.telemetry
    account_fields = telemetry.account_fields(instruction) if hasattr(telemetry, 'account_fields') else {}
    try:
        managed_owner = customer['sub'] if customer and banking and banking.managed_account(instruction.account_number) else None
        result, replayed = store.issue_idempotent(
            CardCreate.model_validate(instruction.model_dump(exclude={'additional_card'})),
            idempotency_key, additional=instruction.additional_card, customer_id=managed_owner)
        card = Card.model_validate(result.model_dump(exclude={'outcome', 'message'}))
    except AccountNotOwned:
        raise HTTPException(403, 'Account is not authorized for this customer') from None
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
    response.headers['Card-Issuance-Outcome'] = result.outcome
    response.status_code = 200 if result.outcome == 'already_exists' else 201
    telemetry.emit('log', 'card_replayed' if replayed else 'card_' + result.outcome, trace_id=trace_id,
                   level='INFO', card_id=str(card.id), status=card.status, **account_fields)
    return card
