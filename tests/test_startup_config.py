import json
from contextlib import closing
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from card_servicing.config import ConfigurationError, InfrastructureSettings, load_config
from card_servicing.main import create_app
from card_servicing.models import CardCreate
from card_servicing.persistence import SQLiteCardStore
from card_servicing.ports import StorageUnavailable
from card_servicing.store import CardStore
from card_servicing.telemetry import EVENT_FIELDS, MongoTelemetry, NullTelemetry, StdoutTelemetry

BODY = {'given_name': 'Alex', 'user_name': 'alex.synthetic', 'account_number': '0012345678'}


def configure(monkeypatch, tmp_path, **changes):
    config = {'SQLITE_PATH': str(tmp_path / 'cards.db'), **changes}
    monkeypatch.setattr('card_servicing.main.load_config', lambda: config)
    return config


@pytest.mark.parametrize('sink,expected', [('mongo', MongoTelemetry), ('stdout', StdoutTelemetry), ('none', NullTelemetry)])
def test_selected_adapters_only(monkeypatch, tmp_path, sink, expected):
    config = configure(monkeypatch, tmp_path, TELEMETRY_SINK=sink)
    if sink == 'mongo':
        config.update(MONGO_USER='synthetic-user', MONGO_PASSWORD='synthetic-password')
    else:
        # Irrelevant Mongo settings must neither validate nor create a client.
        config.update(MONGODB_PORT='invalid', MONGO_PASSWORD=None)

    def no_client(*args, **kwargs):
        raise AssertionError('No Mongo client is needed during startup')
    monkeypatch.setattr('card_servicing.telemetry.MongoClient', no_client)
    app = create_app(local_infrastructure=True)
    with TestClient(app):
        assert isinstance(app.state.card_store, SQLiteCardStore)
        assert isinstance(app.state.telemetry, expected)


@pytest.mark.parametrize('changes,field', [
    ({'CARD_STORE': 'postgres-synthetic-secret'}, 'CARD_STORE'),
    ({'TELEMETRY_SINK': 'unknown-synthetic-secret'}, 'TELEMETRY_SINK'),
    ({'SQLITE_PATH': ''}, 'SQLITE_PATH'),
    ({'MONGODB_PORT': 'not-a-port'}, 'MONGODB_PORT'),
    ({'MONGODB_PORT': '65536'}, 'MONGODB_PORT'),
    ({'MONGODB_HOST': 'mongodb://secret@host'}, 'MONGODB_HOST'),
    ({'MONGODB_DATABASE': 'bad/name'}, 'MONGODB_DATABASE'),
    ({'MONGO_USER': ''}, 'MONGO_USER'),
    ({'MONGO_PASSWORD': None}, 'MONGO_PASSWORD'),
])
def test_invalid_selected_config_fails_startup_safely(monkeypatch, tmp_path, changes, field):
    config = configure(monkeypatch, tmp_path, MONGO_USER='synthetic-user', MONGO_PASSWORD='synthetic-secret')
    config.update(changes)
    with pytest.raises(ConfigurationError) as caught:
        with TestClient(create_app(local_infrastructure=True)):
            pass
    assert field in str(caught.value)
    assert 'synthetic-secret' not in str(caught.value)
    assert 'secret@host' not in str(caught.value)
    assert not (tmp_path / 'cards.db').exists()


def test_injected_instances_win_even_over_invalid_provider_config(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, CARD_STORE='unknown', TELEMETRY_SINK='unknown')
    store, telemetry = CardStore(), NullTelemetry()
    app = create_app(local_infrastructure=True, card_store=store, telemetry=telemetry)
    with TestClient(app) as client:
        assert app.state.card_store is store
        assert app.state.telemetry is telemetry
        assert client.post('/cards', json=BODY, headers={'Idempotency-Key': 'injected'}).status_code == 201
    assert not (tmp_path / 'cards.db').exists()


@pytest.mark.parametrize('inject_store', [False, True])
def test_partial_injection_only_selects_missing_adapter(monkeypatch, tmp_path, inject_store):
    config = configure(monkeypatch, tmp_path, TELEMETRY_SINK='stdout')
    injected = CardStore() if inject_store else NullTelemetry()
    if inject_store:
        config['CARD_STORE'] = 'ignored'
        app = create_app(local_infrastructure=True, card_store=injected)
    else:
        config['TELEMETRY_SINK'] = 'ignored'
        app = create_app(local_infrastructure=True, telemetry=injected)
    with TestClient(app):
        assert (app.state.card_store if inject_store else app.state.telemetry) is injected
        assert isinstance(app.state.telemetry if inject_store else app.state.card_store,
                          StdoutTelemetry if inject_store else SQLiteCardStore)


def test_environment_overrides_dotenv(monkeypatch, tmp_path):
    path = tmp_path / '.env'
    path.write_text('TELEMETRY_SINK=mongo\nCARD_STORE=sqlite\nSQLITE_PATH=example.db\n')
    monkeypatch.setenv('TELEMETRY_SINK', 'stdout')
    monkeypatch.delenv('CARD_STORE', raising=False)
    monkeypatch.delenv('SQLITE_PATH', raising=False)
    config = InfrastructureSettings.from_mapping(load_config(path))
    assert config.telemetry_sink == 'stdout'
    assert config.sqlite_path.name == 'example.db'


def test_stdout_creation_replay_allowlist_and_no_mongo(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path, TELEMETRY_SINK='stdout', ACCOUNT_HASH_KEY='x' * 32)
    def forbidden(*args, **kwargs):
        pytest.fail('stdout must not construct Mongo telemetry or client')
    monkeypatch.setattr('card_servicing.main.MongoTelemetry', forbidden)
    monkeypatch.setattr('card_servicing.telemetry.MongoClient', forbidden)
    with TestClient(create_app(local_infrastructure=True)) as client:
        first = client.post('/cards', json=BODY, headers={'Idempotency-Key': 'synthetic-key'})
        replay = client.post('/cards', json={k: ' ' + v + ' ' for k, v in BODY.items()},
                             headers={'Idempotency-Key': 'synthetic-key'})
        assert first.status_code == replay.status_code == 201
        assert first.json() == replay.json()
        assert replay.headers['Idempotency-Replayed'] == 'true'
        assert first.json()['account_number'] == BODY['account_number']
        assert client.get('/health/ready').json() == {'status': 'ready', 'checks': {'sqlite': 'ok'}}
    sink = StdoutTelemetry({})
    sink.emit('log', 'synthetic-check', account_number=BODY['account_number'],
              request_body=BODY, ANTHROPIC_API_KEY='synthetic-secret', password='synthetic-password')
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]
    assert {'card_created', 'card_replayed', 'http_request'}.issubset({r['event'] for r in records})
    assert all(set(r) <= EVENT_FIELDS | {'_id', 'timestamp', 'kind', 'event'} for r in records)
    assert all(value not in output for value in (BODY['account_number'], BODY['given_name'],
               BODY['user_name'], 'synthetic-secret', 'synthetic-password', 'synthetic-key', 'SIM-'))


def test_runtime_storage_outage_is_not_invalid_config(monkeypatch, tmp_path):
    blocker = tmp_path / 'not-a-directory'
    blocker.write_text('blocked')
    configure(monkeypatch, tmp_path, TELEMETRY_SINK='none', SQLITE_PATH=str(blocker / 'cards.db'))
    with TestClient(create_app(local_infrastructure=True)) as client:
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').json() == {'status': 'not_ready', 'checks': {'sqlite': 'unavailable'}}
        assert client.post('/cards', json=BODY, headers={'Idempotency-Key': 'outage'}).status_code == 503


def test_selected_mongo_outage_keeps_api_ready(monkeypatch, tmp_path, caplog):
    configure(monkeypatch, tmp_path, MONGO_USER='synthetic', MONGO_PASSWORD='synthetic-secret')
    def offline(*args, **kwargs):
        raise OSError('synthetic-secret')
    monkeypatch.setattr('card_servicing.telemetry.MongoClient', offline)
    with TestClient(create_app(local_infrastructure=True)) as client:
        assert client.get('/health/ready').status_code == 200
        assert client.post('/cards', json=BODY, headers={'Idempotency-Key': 'offline'}).status_code == 201
    assert 'mongodb_telemetry_failed' in caplog.text
    assert 'synthetic-secret' not in caplog.text


def test_http_works_with_provider_neutral_store_contract():
    class UnavailableStore:
        dependency_name = 'synthetic_provider'
        def initialize(self):
            raise StorageUnavailable()
        def check_readiness(self):
            raise StorageUnavailable()
        def create_idempotent(self, instruction, key):
            raise StorageUnavailable()
        def issue_idempotent(self, instruction, key, **kwargs):
            raise StorageUnavailable()
    with TestClient(create_app(card_store=UnavailableStore())) as client:
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').json()['checks'] == {'synthetic_provider': 'unavailable'}
        assert client.post('/cards', json=BODY, headers={'Idempotency-Key': 'outage'}).json() == {'detail': 'Card storage unavailable'}


def test_previous_schema_database_replays_after_additive_migration(tmp_path):
    path = tmp_path / 'legacy.db'
    with closing(sqlite3.connect(path)) as db:
        db.executescript((Path(__file__).parent / 'fixtures' / 'legacy_cards.sql').read_text())
        expected = json.loads(db.execute('SELECT response_json FROM card_idempotency').fetchone()[0])
    with TestClient(create_app(card_store=SQLiteCardStore(str(path)))) as client:
        replay = client.post('/cards', json=BODY, headers={'Idempotency-Key': 'legacy-synthetic'})
        assert replay.status_code == 201
        assert replay.json() == expected
        assert replay.headers['Idempotency-Replayed'] == 'true'
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='country_declaration_history'").fetchone()
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM card_idempotency').fetchone()[0] == 1
