"""Exercise the real async Mongo adapter with an in-process Mongo client double."""
import logging
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from card_chat.observability import instrument


@pytest.mark.parametrize('service,logger,event', [
    ('agent', 'card_chat.provider', 'model_usage'),
    ('agent', 'card_chat.main', 'workflow_usage'),
    ('mcp', 'card_chat.mcp_server', 'tool_execution'),
    ('experience', 'card_chat.experience', None),
])
def test_mongo_service_events_and_routes_are_redacted(monkeypatch, service, logger, event):
    records, indexes, closed = [], [], []
    class Collection:
        def __init__(self, name): self.name = name
        def create_index(self, field, **kwargs): indexes.append((self.name, field, kwargs))
        def insert_one(self, record): records.append((self.name, record))
    class Database:
        def __getitem__(self, name): return Collection(name)
    class Client:
        def __init__(self, *args, **kwargs):
            assert args == ('mongodb', 27017)
            assert kwargs['password'] == 'mongodb-password'
        def __getitem__(self, name): return Database()
        def close(self): closed.append(True)
    monkeypatch.setattr('card_servicing.telemetry.MongoClient', Client)
    app = FastAPI()
    instrument(app, {'TELEMETRY_SINK':'mongo','MONGODB_HOST':'mongodb','MONGODB_PORT':'27017',
                     'MONGO_USER':'synthetic','MONGO_PASSWORD':'mongodb-password'}, service)
    @app.post('/examples/{identifier}')
    async def operation(identifier: str):
        log = logging.getLogger(logger)
        log.warning('private exception text must stay out of Mongo')
        if event:
            log.warning('model prompt secret', extra={'telemetry_event':event,
                'telemetry_fields':{'input_tokens':12, 'tool':'create_card',
                                    'password':'private-password', 'prompt':'private-prompt'}})
        return {'ok':True}
    before = list(logging.getLogger('card_chat').handlers)
    with TestClient(app) as client:
        response = client.post('/examples/private-customer', json={'password':'private-body'},
                               headers={'X-Correlation-ID':'a'*32})
        assert response.status_code == 200
    assert list(logging.getLogger('card_chat').handlers) == before
    assert closed
    assert any(name=='request_traces' and record['route']=='/examples/{identifier}'
               and record['trace_id']=='a'*32 and record['service']==service for name,record in records)
    if event:
        assert any(name=='application_logs' and record['event']==event and record['input_tokens']==12 for name,record in records)
    output = str(records)
    for secret in ('private-password','private-prompt','private-body','private-customer',
                   'mongodb-password','model prompt secret','private exception text'):
        assert secret not in output
    assert any(kwargs.get('expireAfterSeconds')==604800 for _,_,kwargs in indexes)


def test_mongo_failure_does_not_fail_http_or_echo_credentials(monkeypatch, caplog):
    def offline(*args, **kwargs):
        raise OSError('mongodb-password')
    monkeypatch.setattr('card_servicing.telemetry.MongoClient', offline)
    app = FastAPI()
    instrument(app, {'TELEMETRY_SINK':'mongo','MONGO_USER':'synthetic',
                     'MONGO_PASSWORD':'mongodb-password'}, 'experience')
    @app.get('/health')
    async def health(): return {'status':'ok'}
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
    assert 'mongodb_telemetry_failed' in caplog.text
    assert 'mongodb-password' not in caplog.text
