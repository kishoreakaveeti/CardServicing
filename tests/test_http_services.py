import json
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from fastapi.testclient import TestClient

from card_chat.main import create_app as real_agent_app
from card_chat.security import Security
from card_chat.turns import ProposedToolCall
from card_chat.durable import Journal
from card_servicing.main import create_app as cards_app
from test_chat import BODY, StubModel, Gateway, create_app, prepare_review


@pytest.fixture
def identity(tmp_path, monkeypatch):
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key = ed25519.Ed25519PrivateKey.generate()
    private, public = tmp_path / 'private.pem', tmp_path / 'public.pem'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    config = {'OIDC_ISSUER': 'https://identity.test/realms/cards', 'OIDC_JWKS_URL': 'https://identity.test/certs',
              'DELEGATION_PRIVATE_KEY': str(private), 'DELEGATION_PUBLIC_KEY': str(public),
              'AGENT_DB': str(tmp_path / 'agent.db')}
    monkeypatch.setattr(jwt.PyJWKClient, 'get_signing_key_from_jwt', lambda *args: SimpleNamespace(key=rsa_key.public_key()))
    def headers(user='alex', audience='agent', permission='agent:invoke', client='experience-service', **changes):
        now = int(time.time())
        claims = {'iss': config['OIDC_ISSUER'], 'aud': audience, 'sub': client, 'azp': client,
                  'exp': now + 60, 'iat': now, 'scope': permission, **changes}
        customer = Security(config).issue_customer({'sub': user, 'preferred_username': 'alex.synthetic',
                                                   'accounts': ['0012345678']})
        return {'Authorization': 'Bearer ' + jwt.encode(claims, rsa_key, algorithm='RS256'),
                'X-Customer-Delegation': customer}
    return config, headers


def test_authenticated_async_dedup_and_ownership(identity):
    config, headers = identity
    model, gateway = StubModel(), Gateway()
    model.outputs = [ProposedToolCall('create_card', BODY)]
    with TestClient(create_app(model, gateway, config=config)) as client:
        good = headers()
        assert client.post('/conversations').status_code == 401
        cid = client.post('/conversations', headers=good).json()['id']
        path = f'/conversations/{cid}'
        assert client.post(path + '/messages', json={'text': 'x'}, headers=headers('sam')).status_code == 404
        prepare_review(client, cid, headers=good)
        draft = client.app.state.journal.load(cid, 'alex')['draft']['arguments']
        model.outputs = [ProposedToolCall('create_card', draft)]
        h = {**good, 'Prefer': 'respond-async', 'Idempotency-Key': 'message-one'}
        accepted = client.post(path + '/messages', json={'text': 'x'}, headers=h)
        assert accepted.status_code == 202
        status = accepted.json()['status_url']
        assert client.get(status, headers=headers('sam')).status_code == 404
        for _ in range(100):
            job = client.get(status, headers=good).json()
            if job['status'] == 'succeeded':
                break
            time.sleep(.01)
        assert job['result']['review']
        assert gateway.calls == []
        duplicate = client.post(path + '/messages', json={'text': 'x'}, headers=h).json()
        assert duplicate['id'] == accepted.json()['id'] and len(model.seen_tools) == 1
        assert client.post(path + '/messages', json={'text': 'y'}, headers=h).status_code == 409
        assert client.post(path + '/confirm', json={'review': 'stale'}, headers=good).status_code == 409
        confirm = client.post(path + '/confirm', json={'review': job['result']['review']},
                              headers={**good, 'Prefer': 'respond-async', 'Idempotency-Key': 'confirm-one'})
        for _ in range(100):
            completed = client.get(confirm.json()['status_url'], headers=good).json()
            if completed['status'] == 'succeeded':
                break
            time.sleep(.01)
        assert 'SIM-' in completed['result']['message']
        assert len(gateway.calls) == 1


@pytest.mark.parametrize('change,expected', [
    ({'audience': 'cards'}, 401), ({'exp': 1}, 401), ({'iss': 'https://evil.test'}, 401),
    ({'permission': 'wrong'}, 403), ({'client': 'mcp-service'}, 403)])
def test_destination_token_validation(identity, change, expected):
    config, headers = identity
    with TestClient(create_app(StubModel(), Gateway(), config=config)) as client:
        assert client.post('/conversations', headers=headers(**change)).status_code == expected
        assert client.post('/conversations', headers={'Authorization': 'Bearer broken'}).status_code == 401


def test_review_and_uncertain_key_survive_restart(tmp_path):
    config = {'AGENT_DB': str(tmp_path / 'agent.db')}
    model, gateway = StubModel(), Gateway()
    model.outputs = [ProposedToolCall('create_card', BODY)]
    with TestClient(create_app(model, gateway, config=config)) as client:
        cid = client.post('/conversations').json()['id']
        path = f'/conversations/{cid}'
        review = prepare_review(client, cid).json()['review']
        gateway.fail = True
        assert 'retry' in client.post(path + '/confirm', json={'review': review}).json()['message']
    with TestClient(create_app(StubModel(), gateway, config=config)) as client:
        result = client.post(path + '/confirm', json={'review': review}).json()
        assert 'SIM-' in result['message']
        assert gateway.calls[0] == gateway.calls[1]
        client.post(path + '/confirm', json={'review': review})
        assert len(gateway.calls) == 2


def test_accepted_running_job_recovers_after_restart(tmp_path):
    config = {'AGENT_DB': str(tmp_path / 'agent.db')}
    gateway, model = Gateway(), StubModel()
    model.outputs = [ProposedToolCall('create_card', BODY)]
    with TestClient(create_app(model, gateway, config=config)) as client:
        cid = client.post('/conversations').json()['id']
        review = prepare_review(client, cid).json()['review']
    journal = Journal(config['AGENT_DB'])
    oid = uuid4().hex
    journal.db.execute("INSERT INTO operations(id,owner,cid,kind,request_key,body,status) VALUES(?,?,?,?,?,?,'running')",
                       (oid, 'isolated-test', cid, 'confirm', 'recover-one', json.dumps({'review': review})))
    journal.db.commit()
    journal.close()
    with TestClient(create_app(StubModel(), gateway, config=config)) as client:
        for _ in range(100):
            result = client.get('/operations/' + oid).json()
            if result['status'] == 'succeeded':
                break
            time.sleep(.01)
        assert 'SIM-' in result['result']['message']
        assert len(gateway.calls) == 1


def test_cards_enforces_customer_account(identity):
    config, headers = identity
    with TestClient(cards_app(security_config=config)) as client:
        h = {**headers(audience='cards', permission='cards:write', client='mcp-service'), 'Idempotency-Key': 'authorized'}
        assert client.post('/cards', json={**BODY, 'account_number': '0098765432'}, headers=h).status_code == 403
        assert client.post('/cards', json={**BODY, 'user_name': 'sam.synthetic'}, headers=h).status_code == 403
        assert client.post('/cards', json=BODY, headers=h).status_code == 201
        assert client.post('/cards', json=BODY, headers={**h, 'X-Customer-Delegation': 'tampered'}).status_code == 401


def test_service_entrypoints_require_identity_configuration():
    from card_chat.mcp_server import create_app as mcp_app
    from card_chat.experience import create_app as experience_app
    for factory in (real_agent_app, mcp_app, experience_app):
        with pytest.raises(RuntimeError, match='Configure OIDC'):
            with TestClient(factory(config={})):
                pass
