"""Test-only second operation. This is not included in the production API."""
from pydantic import BaseModel, ConfigDict, Field

from card_servicing.main import create_app

app = create_app()
calls = []


class BlockRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=100, description='Customer-supplied reason for blocking the synthetic card.')


class BlockResult(BaseModel):
    card_id: str
    status: str


class Stats(BaseModel):
    writes: int
    card_id: str
    reason: str


@app.post('/cards/{card_id}/block', operation_id='block_card', response_model=BlockResult)
def block_card(card_id: str, body: BlockRequest):
    calls.append((card_id, body.reason))
    return {'card_id': card_id, 'status': 'blocked'}


@app.get('/test-stats', operation_id='test_stats', response_model=Stats)
def stats():
    card_id, reason = calls[-1] if calls else ('', '')
    return {'writes': len(calls), 'card_id': card_id, 'reason': reason}
