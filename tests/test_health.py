from contextlib import closing
import sqlite3

import pytest
from fastapi.testclient import TestClient

from card_servicing.main import create_app
from card_servicing.persistence import SQLiteCardStore
from card_servicing.telemetry import MongoTelemetry


def test_readiness_requires_lifespan_and_supports_memory_store():
    app = create_app()
    client = TestClient(app)
    assert client.get('/health/ready').status_code == 503
    with client:
        result = client.get('/health/ready')
        assert result.status_code == 200
        assert result.json() == {'status': 'ready', 'checks': {'card_store': 'ok'}}
    assert client.get('/health/ready').status_code == 503


def test_sqlite_ready_probe_does_not_write(tmp_path):
    path = tmp_path / 'cards.db'
    store = SQLiteCardStore(str(path))
    with TestClient(create_app(card_store=store)) as client:
        # BEGIN IMMEDIATE reserves the writer slot but permits readers. A probe
        # must still pass; it should never contend as a second writer.
        with closing(store.connect()) as writer:
            writer.execute('BEGIN IMMEDIATE')
            result = client.get('/health/ready')
            assert result.status_code == 200
            assert result.json() == {'status': 'ready', 'checks': {'sqlite': 'ok'}}
            assert result.headers['Cache-Control'] == 'no-store'
            writer.rollback()
        with closing(store.connect()) as db:
            assert db.execute('SELECT count(*) FROM cards').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM card_idempotency').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['missing_file', 'missing_table', 'missing_status', 'corrupt_file'])
def test_storage_failure_returns_sanitized_503_and_keeps_liveness(tmp_path, failure):
    path = tmp_path / 'cards.db'
    store = SQLiteCardStore(str(path))
    with TestClient(create_app(card_store=store)) as client:
        if failure == 'missing_file':
            path.unlink()
        elif failure == 'corrupt_file':
            path.write_bytes(b'not a database')
        else:
            with closing(store.connect()) as db, db:
                db.execute('DROP TABLE card_idempotency' if failure == 'missing_table' else 'DELETE FROM card_statuses')
        result = client.get('/health/ready')
        assert result.status_code == 503
        assert result.json() == {'status': 'not_ready', 'checks': {'sqlite': 'unavailable'}}
        for endpoint in ('/health', '/health/live'):
            assert client.get(endpoint).json() == {'status': 'ok'}
            assert client.get(endpoint).status_code == 200
        if failure == 'missing_file':
            assert not path.exists()  # A probe must not silently recreate it.


def test_failed_startup_stays_unready_without_reinitializing_in_probe(tmp_path):
    blocker = tmp_path / 'file'
    blocker.write_text('not a directory')
    store = SQLiteCardStore(str(blocker / 'cards.db'))
    with TestClient(create_app(card_store=store)) as client:
        assert not store.ready
        blocker.unlink()
        result = client.get('/health/ready')
        assert result.status_code == 503
        assert not blocker.exists()  # The probe does not retry schema creation.
        assert client.get('/health/live').status_code == 200


def test_readiness_recovers_after_transient_read_error(tmp_path, monkeypatch):
    store = SQLiteCardStore(str(tmp_path / 'cards.db'))
    with TestClient(create_app(card_store=store)) as client:
        with monkeypatch.context() as patch:
            def failed_connect(*args, **kwargs):
                raise sqlite3.OperationalError('sensitive path and SQL details')
            patch.setattr('card_servicing.persistence.sqlite3.connect', failed_connect)
            result = client.get('/health/ready')
            assert result.status_code == 503
            assert 'sensitive' not in result.text
        assert client.get('/health/ready').status_code == 200


def test_mongodb_failure_does_not_affect_card_readiness(tmp_path, monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise OSError('mongo offline')
    monkeypatch.setattr('card_servicing.telemetry.MongoClient', unavailable)
    store = SQLiteCardStore(str(tmp_path / 'cards.db'))
    with TestClient(create_app(card_store=store, telemetry=MongoTelemetry({}))) as client:
        assert client.get('/health/ready').status_code == 200
        assert client.get('/health/live').status_code == 200
    assert 'mongodb_telemetry_failed' in caplog.text
