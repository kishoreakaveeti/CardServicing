from concurrent.futures import ThreadPoolExecutor
import pytest
from card_servicing.models import CardCreate
from card_servicing.persistence import SQLiteCardStore
from card_servicing.store import CardStore
from card_servicing.idempotency import IdempotencyConflict

@pytest.mark.parametrize('sqlite', [False, True])
def test_conflict_and_concurrent_replay(tmp_path, sqlite):
    path=str(tmp_path/'cards.db')
    store=SQLiteCardStore(path) if sqlite else CardStore()
    if sqlite: store.initialize()
    body=CardCreate(user_name='alex', given_name='Alex', account_number='001')
    def create(_):
        adapter=SQLiteCardStore(path) if sqlite else store
        return adapter.create_idempotent(body, 'same-key')
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(create, range(12)))
    assert len({str(card.id) for card,_ in results}) == 1
    assert sum(not replay for _,replay in results) == 1
    with pytest.raises(IdempotencyConflict):
        store.create_idempotent(body.model_copy(update={'account_number':'002'}), 'same-key')
