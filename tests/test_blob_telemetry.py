import json
from unittest.mock import Mock
import pytest
from card_servicing.config import InfrastructureSettings, ConfigurationError
from card_servicing.telemetry import AzureBlobTelemetry


def config():
    return {'TELEMETRY_SINK': 'azure_blob',
            'AZURE_STORAGE_ACCOUNT_URL': 'https://demo.blob.core.windows.net',
            'AZURE_STORAGE_CONTAINER': 'telemetry'}


def test_blob_selection_and_secret_isolation():
    settings = InfrastructureSettings.from_mapping({**config(), 'ANTHROPIC_API_KEY': 'secret'}, select_store=False)
    assert settings.telemetry_sink == 'azure_blob'
    assert 'ANTHROPIC_API_KEY' not in settings.telemetry_config


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:secret@example.com', 'https://example.com?sig=secret'])
def test_reject_unsafe_account_urls(url):
    with pytest.raises(ConfigurationError):
        InfrastructureSettings.from_mapping({**config(), 'AZURE_STORAGE_ACCOUNT_URL': url}, select_store=False)


def test_blob_batching_schema_and_unique_names(capsys):
    client = Mock()
    sink = AzureBlobTelemetry(config(), batch_size=2, flush_seconds=0.01, client_factory=lambda: client)
    sink.emit('trace', 'http_request', trace_id='a'*32, service='agent', password='private')
    sink.emit('log', 'model_usage', service='agent')
    sink.start()
    sink.queue.join()
    sink.emit('log', 'tool_execution')
    sink.queue.join()
    sink.close()
    calls = client.get_blob_client.call_args_list
    assert len(calls) == 2
    assert calls[0].args[1] != calls[1].args[1]
    uploads = client.get_blob_client.return_value.upload_blob.call_args_list
    records = [json.loads(line) for line in uploads[0].args[0].splitlines()]
    assert len(records) == 2
    assert records[0]['trace_id'] == 'a'*32
    assert records[0]['service'] == 'agent'
    assert 'timestamp' in records[0] and 'password' not in records[0]
    assert uploads[0].kwargs['overwrite'] is False
    assert 'private' not in capsys.readouterr().out
    client.close.assert_called_once()


def test_blob_failure_is_bounded_and_redacts(caplog):
    client = Mock()
    client.get_blob_client.return_value.upload_blob.side_effect = OSError('secret SAS token')
    sink = AzureBlobTelemetry(config(), flush_seconds=0.01, client_factory=lambda: client)
    sink.start()
    sink.emit('log', 'banking_completed')
    sink.queue.join()
    sink.close()
    assert client.get_blob_client.return_value.upload_blob.call_count == 3
    assert 'azure_blob_telemetry_failed' in caplog.text
    assert 'secret SAS token' not in caplog.text


def test_blob_client_initialization_failure_does_not_escape(caplog):
    def unavailable():
        raise OSError('credential secret')
    sink = AzureBlobTelemetry(config(), flush_seconds=0.01, client_factory=unavailable)
    sink.start()
    sink.emit('log', 'banking_completed')
    sink.queue.join()
    sink.close()
    assert 'azure_blob_telemetry_failed' in caplog.text
    assert 'credential secret' not in caplog.text


def test_queue_full_is_nonblocking(caplog):
    sink = AzureBlobTelemetry(config(), queue_size=1)
    sink.emit('log', 'first')
    sink.emit('log', 'second')
    assert sink.queue.qsize() == 1
    assert 'telemetry_queue_full' in caplog.text


def test_blob_outage_preserves_sqlite_creation_and_replay(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from card_servicing.main import create_app
    monkeypatch.setattr('card_servicing.main.load_config', lambda: {
        **config(), 'SQLITE_PATH': str(tmp_path / 'cards.db')})
    def unavailable(self):
        raise OSError('secret credential')
    monkeypatch.setattr(AzureBlobTelemetry, '_client', unavailable)
    with TestClient(create_app(local_infrastructure=True)) as client:
        body = {'given_name': 'Alex', 'user_name': 'alex.synthetic', 'account_number': '0012345678'}
        first = client.post('/cards', json=body, headers={'Idempotency-Key': 'blob-outage'})
        replay = client.post('/cards', json=body, headers={'Idempotency-Key': 'blob-outage'})
        assert first.status_code == replay.status_code == 201
        assert first.json() == replay.json()
        assert replay.headers['Idempotency-Replayed'] == 'true'
        assert client.get('/health/ready').status_code == 200


def test_ambiguous_upload_conflict_is_not_retried():
    from azure.core.exceptions import ResourceExistsError
    client = Mock()
    client.get_blob_client.return_value.upload_blob.side_effect = ResourceExistsError('exists')
    sink = AzureBlobTelemetry(config(), flush_seconds=0.01, client_factory=lambda: client)
    sink.start()
    sink.emit('trace', 'http_request')
    sink.queue.join()
    sink.close()
    assert client.get_blob_client.return_value.upload_blob.call_count == 1


def test_connection_string_requires_https():
    with pytest.raises(ConfigurationError, match='HTTPS'):
        InfrastructureSettings.from_mapping({**config(), 'AZURE_STORAGE_CONNECTION_STRING':
            'DefaultEndpointsProtocol=http;AccountName=demo;AccountKey=secret'}, select_store=False)
