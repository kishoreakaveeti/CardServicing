from contextlib import closing
import sqlite3
import pytest
from fastapi.testclient import TestClient
from card_servicing.main import create_app
from card_servicing.models import CardCreate
from card_servicing.persistence import SQLiteCardStore
from card_servicing.telemetry import MongoTelemetry
from card_servicing.ports import StorageUnavailable

BODY={'user_name':'alex', 'given_name':'Alex', 'account_number':'001'}

def test_restart_replay_and_readable_fields(tmp_path):
    path=str(tmp_path/'cards.db')
    def request(body):
        with TestClient(create_app(card_store=SQLiteCardStore(path))) as client:
            return client.post('/cards', json=body, headers={'Idempotency-Key':'persist'})
    first=request(BODY)
    assert first.status_code == 201
    assert request(BODY).json() == first.json()
    assert request({**BODY, 'given_name':'Other'}).status_code == 409
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT user_name,given_name,account_number FROM cards').fetchall() == [('alex','Alex','001')]
        assert db.execute('SELECT count(*) FROM card_idempotency').fetchone()[0] == 1

def test_atomic_rollback(tmp_path):
    store=SQLiteCardStore(str(tmp_path/'cards.db')); store.initialize()
    with closing(store.connect()) as db, db:
        db.execute("CREATE TRIGGER fail_snapshot BEFORE INSERT ON card_idempotency BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(StorageUnavailable):
        store.create_idempotent(CardCreate(**BODY), 'rollback')
    with closing(store.connect()) as db:
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0] == 0

def test_sanitized_storage_failure(tmp_path, monkeypatch):
    store=SQLiteCardStore(str(tmp_path/'cards.db'))
    with TestClient(create_app(card_store=store)) as client:
        def failed(*args): raise sqlite3.OperationalError('sensitive details')
        monkeypatch.setattr(store, 'connect', failed)
        response=client.post('/cards', json=BODY, headers={'Idempotency-Key':'fail'})
        assert response.status_code == 503
        assert 'sensitive' not in response.text

def test_diagnostics_redact_and_fallback(monkeypatch, caplog):
    telemetry=MongoTelemetry({'ACCOUNT_HASH_KEY':'x'*32})
    fields=telemetry.account_fields(CardCreate(**BODY))
    assert set(fields) == {'account_number_hash','account_hash_key_version'}
    assert len(fields['account_number_hash']) == 64
    assert MongoTelemetry({}).account_fields(CardCreate(**BODY)) == {}
    def offline(*args,**kwargs): raise OSError('sensitive message')
    monkeypatch.setattr('card_servicing.telemetry.MongoClient', offline)
    with TestClient(create_app(telemetry=telemetry)) as client:
        assert client.post('/cards', json=BODY, headers={'Idempotency-Key':'safe'}).status_code == 201
    assert 'mongodb_telemetry_failed' in caplog.text
    assert 'sensitive message' not in caplog.text
    assert 'given_name' not in caplog.text and 'SIM-' not in caplog.text

def test_queue_overflow(caplog):
    telemetry=MongoTelemetry({}, queue_size=1)
    telemetry.emit('log','one'); telemetry.emit('log','two')
    assert 'telemetry_queue_full' in caplog.text
