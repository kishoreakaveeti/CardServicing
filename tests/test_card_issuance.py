"""Business duplicate protection is independent of client idempotency keys."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from card_servicing.idempotency import IdempotencyConflict
from card_servicing.models import CardCreate
from card_servicing.persistence import SQLiteCardStore
from card_servicing.store import CardStore
from card_servicing.main import create_app
from card_servicing.banking import BankingService, Credentials, AccountCreate
from card_servicing.ports import AccountNotOwned

BODY = CardCreate(user_name='alex',given_name='Alex',account_number='001')


@pytest.mark.parametrize('persistent', [False, True])
def test_concurrent_channels_new_keys_create_only_one_initial_card(tmp_path, persistent):
    path = str(tmp_path/'cards.db')
    store = SQLiteCardStore(path) if persistent else CardStore()
    store.initialize()
    def issue(index):
        adapter = SQLiteCardStore(path) if persistent else store
        return adapter.issue_idempotent(BODY, 'channel-' + str(index))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(issue, range(12)))
    assert len({result.id for result,_ in results}) == 1
    assert sum(result.outcome=='created' for result,_ in results) == 1
    assert sum(result.outcome=='already_exists' for result,_ in results) == 11
    assert all(not replay for _,replay in results)
    # Each distinct request has its own durable decision, even when no write was needed.
    for i, (first, _) in enumerate(results):
        repeated, replayed = store.issue_idempotent(BODY,'channel-' + str(i))
        assert replayed and repeated == first
    if persistent:
        with closing(store.connect()) as db:
            assert db.execute('SELECT count(*) FROM cards').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM card_issuance_requests').fetchone()[0] == 12


@pytest.mark.parametrize('persistent', [False, True])
def test_additional_cards_are_explicit_and_retries_preserve_original_outcome(tmp_path,persistent):
    path = str(tmp_path/'cards.db')
    store = SQLiteCardStore(path) if persistent else CardStore()
    store.initialize()
    initial, _ = store.issue_idempotent(BODY,'first')
    exists, _ = store.issue_idempotent(BODY,'ordinary')
    extra, _ = store.issue_idempotent(BODY,'explicit',additional=True)
    assert exists.id==initial.id and exists.outcome=='already_exists'
    assert extra.id!=initial.id and extra.outcome=='additional_created'
    if persistent:
        store = SQLiteCardStore(path);store.initialize()
    assert store.issue_idempotent(BODY,'first') == (initial,True)
    assert store.issue_idempotent(BODY,'ordinary') == (exists,True)
    assert store.issue_idempotent(BODY,'explicit',additional=True) == (extra,True)
    with pytest.raises(IdempotencyConflict):
        store.issue_idempotent(BODY,'ordinary',additional=True)
    with pytest.raises(IdempotencyConflict):
        store.issue_idempotent(BODY.model_copy(update={'account_number':'002'}),'first')
    assert store.issue_idempotent(BODY,'later')[0].id == initial.id


def test_managed_account_guard_and_legacy_channel_share_the_same_transaction_rule(tmp_path):
    path = str(tmp_path/'cards.db')
    store = SQLiteCardStore(path);store.initialize()
    banking = BankingService(store)
    registered = banking.register(Credentials(username='alex',password='synthetic password'))
    customer = {'sub':registered['sub'],'username':'alex'}
    account = banking.create_account(customer,AccountCreate(account_type='savings'),'account')
    number = account['account_number']
    body = CardCreate(user_name='alex',given_name='alex',account_number=number)
    def issue(i):
        adapter = SQLiteCardStore(path)
        # Alternate banking and compatibility API key namespaces.
        key = (customer['sub'] + ':' if i % 2 else '') + 'channel-' + str(i)
        return adapter.issue_idempotent(body,key,customer_id=customer['sub'])
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(issue,range(10)))
    assert len({r.id for r,_ in results}) == 1
    assert sum(r.outcome=='created' for r,_ in results)==1
    with pytest.raises(AccountNotOwned):
        # Ownership is checked even for a known/replayed operation key.
        store.issue_idempotent(body,'channel-0',customer_id='another-customer')
    assert len(banking.cards(customer['sub'])) == 1


def test_existing_data_preserved_and_old_snapshots_replay_before_existence_check(tmp_path):
    store = SQLiteCardStore(str(tmp_path/'cards.db'));store.initialize()
    # Existing pre-policy data can contain several cards. Keep them all.
    first,_ = store.create_idempotent(BODY,'old-first')
    second,_ = store.create_idempotent(BODY,'old-second')
    # Make legacy chronology explicit: rapid inserts can share a timestamp on
    # Windows, in which case the production UUID tie-break legitimately varies.
    with closing(store.connect()) as db:
        db.execute('UPDATE cards SET created_at=? WHERE id=?',
                   ((first.created_at + timedelta(seconds=1)).isoformat(), str(second.id)))
        db.commit()
    replay, replayed = store.issue_idempotent(BODY,'old-first')
    assert replayed and replay.id==first.id and replay.outcome=='created'
    ordinary,_ = store.issue_idempotent(BODY,'new-key')
    assert ordinary.id==first.id and ordinary.outcome=='already_exists'
    with closing(store.connect()) as db:
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0]==2


def test_api_decision_and_replay_headers_are_stable_across_restart(tmp_path):
    path = str(tmp_path/'cards.db')
    body = BODY.model_dump()
    def post(key, **changes):
        with TestClient(create_app(card_store=SQLiteCardStore(path))) as api:
            return api.post('/cards',json={**body,**changes},headers={'Idempotency-Key':key})
    created = post('first')
    assert created.status_code==201
    duplicate = post('second')
    assert duplicate.status_code==200 and duplicate.json()==created.json()
    assert duplicate.headers['Card-Issuance-Outcome']=='already_exists'
    assert duplicate.headers['Idempotency-Replayed']=='false'
    extra = post('extra',additional_card=True)
    assert extra.status_code==201 and extra.json()['id']!=created.json()['id']
    retry = post('second')
    assert retry.status_code==duplicate.status_code and retry.json()==duplicate.json()
    assert retry.headers['Idempotency-Replayed']=='true'
    assert post('second',additional_card=True).status_code==409
    assert post('invalid',additional_card='true').status_code==422


def test_failed_decision_snapshot_rolls_back_card_and_original_retry_snapshot(tmp_path):
    from card_servicing.ports import StorageUnavailable
    store = SQLiteCardStore(str(tmp_path/'cards.db'));store.initialize()
    with closing(store.connect()) as db, db:
        db.execute("CREATE TRIGGER fail_decision BEFORE INSERT ON card_issuance_requests BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(StorageUnavailable):
        store.issue_idempotent(BODY,'failed')
    with closing(store.connect()) as db:
        for table in ('cards','card_idempotency','card_issuance_requests'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_model_cannot_enable_additional_issuance_without_a_structured_choice():
    from test_chat import StubModel, Gateway, create_app as chat_app, prepare_review
    from card_chat.turns import ProposedToolCall
    model, gateway = StubModel(), Gateway()
    with TestClient(chat_app(model, gateway)) as client:
        cid = client.post('/conversations').json()['id']
        path = '/conversations/' + cid
        ordinary = prepare_review(client,cid,{'account_number':'0012345678'},'issue_card').json()
        assert ordinary['can_confirm']
        model.outputs = [ProposedToolCall('issue_card',{'additional_card':True})]
        rejected = client.post(path+'/messages',json={'text':'Issue another card'}).json()
        assert not rejected['can_confirm'] and rejected['input_tool']=='issue_card'
        assert client.post(path+'/confirm',json={'review':ordinary['review']}).status_code==409
        assert gateway.calls==[]
        explicit = client.post(path+'/inputs',json={'tool':'issue_card','fields':{'additional_card':True}}).json()
        assert explicit['can_confirm'] and 'True' in explicit['message']
        assert gateway.calls==[]
        client.post(path+'/confirm',json={'review':explicit['review']})
        assert gateway.calls[0][1]['additional_card'] is True
