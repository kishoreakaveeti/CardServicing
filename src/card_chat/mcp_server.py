import logging
from time import perf_counter

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from api_client.client import ApiClient
from card_servicing.models import Card, CardCreate
from card_chat.config import settings

mcp = FastMCP('Card servicing', log_level='WARNING')
logger = logging.getLogger(__name__)


@mcp.tool()
def create_card(given_name: str, user_name: str, account_number: str,
                idempotency_key: str) -> dict[str, object]:
    """Create a simulated card for an existing account. Retry with the same key and body."""
    started = perf_counter()
    outcome = 'error'
    try:
        draft = CardCreate(given_name=given_name, user_name=user_name,
                           account_number=account_number)
        with ApiClient(settings().get('CARD_API_BASE_URL', 'http://127.0.0.1:8010')) as client:
            card = Card.model_validate(client.create_card(**draft.model_dump(),
                                                         idempotency_key=idempotency_key))
        outcome = 'created'
        return {'ok': True, 'id': str(card.id), 'card_number': card.card_number}
    except httpx.TimeoutException:
        return {'ok': False, 'error': 'Card API timed out. Retry this submission.'}
    except httpx.HTTPStatusError as exc:
        return {'ok': False, 'error': f'Card API returned HTTP {exc.response.status_code}. Retry this submission.'}
    except (httpx.RequestError, ValidationError, ValueError):
        return {'ok': False, 'error': 'Card API unavailable or invalid response. Retry this submission.'}
    finally:
        logger.warning('tool=create_card duration_ms=%.1f outcome=%s',
                       (perf_counter() - started) * 1000, outcome)


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    mcp.run(transport='stdio')
