"""Customer journey through the existing BFF, agent and Cards service boundaries."""
import json
import sqlite3

import httpx
from fastapi.testclient import TestClient

from card_chat.catalogue import load_catalogue
from card_chat.experience import create_app as experience_app
from card_chat.main import create_app as agent_app
from card_chat.provider import StubModel
from card_chat.security import Security, delegation
from card_servicing.main import create_app
from card_servicing.persistence import SQLiteCardStore
from test_http_services import identity


def test_full_customer_journey_and_cross_customer_access(identity, tmp_path, monkeypatch):
    config, headers = identity
    config = {**config, 'SESSION_DB': str(tmp_path / 'sessions.db'),
              'PUBLIC_ORIGIN': 'http://testserver', 'AGENT_URL': 'http://agent',
              'CARD_API_BASE_URL': 'http://cards'}
    database = tmp_path / 'cards.db'
    with TestClient(create_app(card_store=SQLiteCardStore(str(database)), security_config=config)) as api:
        service_headers = headers()
        assert api.post('/banking/register', headers=headers(audience='cards',permission='cards:write',client='mcp-service'), json={'username':'outsider','password':'synthetic password'}).status_code == 403
        assert api.post('/banking/register', headers={**headers(client='mcp-service'),'X-Banking-BFF':'1'}, json={'username':'outsider','password':'synthetic password'}).status_code == 403

        async def bff_headers(self):
            return {'Authorization': service_headers['Authorization'],
                    'X-Customer-Delegation': delegation.get() or '', 'X-Correlation-ID': 'a' * 32}
        monkeypatch.setattr(Security, 'headers', bff_headers)

        class Gateway:
            catalogue = load_catalogue()
            async def execute(self, name, arguments, key=None):
                spec = self.catalogue[name]
                h = headers(audience='cards', permission='cards:write', client='mcp-service')
                h['X-Customer-Delegation'] = delegation.get()
                if key:
                    h['Idempotency-Key'] = key
                response = api.request(spec.method, spec.path, json=arguments if spec.method != 'GET' else None, headers=h)
                return {'ok': response.is_success, 'result': response.json()}

        class RecordingModel(StubModel):
            contexts = []
            async def turn(self, history, collected, tools):
                self.contexts.append((history, collected, tools))
                return await super().turn(history, collected, tools)
        model = RecordingModel()
        with TestClient(agent_app(model, Gateway(), config=config)) as agent:
            original_client = httpx.AsyncClient
            def route(request):
                target = api if request.url.host == 'cards' else agent
                response = target.request(request.method, request.url.path, content=request.content, headers=dict(request.headers))
                return httpx.Response(response.status_code, json=response.json())
            monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(**{**kwargs, 'transport': httpx.MockTransport(route)}))

            with TestClient(experience_app(config)) as browser:
                origin = {'Origin': 'http://testserver'}
                assert browser.get('/api/session').status_code == 401
                assert browser.post('/auth/register', json={'username':'alex', 'password':'synthetic password'}).status_code == 403
                intent = browser.post('/api/entry/messages', headers=origin, json={'text':'I want to register'}).json()
                assert intent['input_tool'] == 'register'
                result = browser.post('/auth/register', headers=origin, json={'username':'alex', 'password':'synthetic password'})
                assert result.status_code == 201
                assert 'HttpOnly' in result.headers['set-cookie']
                csrf = {**origin, 'X-CSRF-Token': result.json()['csrf']}
                first_cookie = browser.cookies.get('card_session')
                assert browser.post('/api/conversations', json={}).status_code == 403

                def start():
                    response = browser.post('/api/conversations', headers=csrf, json={})
                    assert response.status_code == 200, response.text
                    return '/api/conversations/' + response.json()['id']

                path = start()
                empty = browser.post(path+'/messages', headers=csrf, json={'text':'Show my accounts'}).json()
                assert empty['data']['accounts'] == []
                assert 'Create account' in empty['message']
                prompt = browser.post(path+'/messages', headers=csrf, json={'text':'I want a new debit card'}).json()
                assert prompt['input_tool'] == 'create_account'
                # Direct selection uses the same structured workflow, without a model call.
                review = browser.post(path+'/inputs', headers=csrf, json={'tool':'create_account', 'fields':{'account_type':'savings', 'issue_card':True}}).json()
                assert review['can_confirm'] and review['usage']['model_calls'] == 2
                with sqlite3.connect(database) as db:
                    assert db.execute('SELECT count(*) FROM accounts').fetchone()[0] == 0
                done = browser.post(path+'/confirm', headers=csrf, json={'review':review['review']})
                assert 'succeeded' in done.json()['message'], done.text
                assert browser.post(path+'/confirm', headers=csrf, json={'review':review['review']}).json() == done.json()
                path = start()
                accounts = browser.post(path+'/messages', headers=csrf, json={'text':'Show my accounts'}).json()['data']['accounts']
                assert len(accounts) == 1
                number = accounts[0]['account_number']
                options = browser.get(path+'/inputs').json()['tools']
                issue = next(t for t in options if t['name']=='issue_card')
                ref = issue['fields'][0]['options'][0]['ref']
                review = browser.post(path+'/inputs', headers=csrf, json={'tool':'issue_card','selections':{'account_number':ref}}).json()
                existing = browser.post(path+'/confirm', headers=csrf, json={'review':review['review']}).json()
                assert 'A card already exists' in existing['message']
                assert 'succeeded' not in existing['message']
                path = start()
                prompted = browser.post(path+'/messages', headers=csrf, json={'text':'I want a new card'}).json()
                assert prompted['input_tool'] == 'issue_card'
                options = browser.get(path+'/inputs').json()['tools']
                issue = next(t for t in options if t['name']=='issue_card')
                ref = next(f for f in issue['fields'] if f['name']=='account_number')['options'][0]['ref']
                review = browser.post(path+'/inputs', headers=csrf, json={'tool':'issue_card',
                    'fields':{'additional_card':True},'selections':{'account_number':ref}}).json()
                assert 'additional card' in review['message'] and 'True' in review['message']
                with sqlite3.connect(database) as db:
                    assert db.execute('SELECT count(*) FROM cards').fetchone()[0] == 1
                additional = browser.post(path+'/confirm', headers=csrf,json={'review':review['review']}).json()
                assert 'additional simulated card was created' in additional['message']
                path = start()
                cards = browser.post(path+'/messages', headers=csrf, json={'text':'Show all my cards'}).json()['data']['cards']
                assert len(cards) == 2 and all(c['card_number'].startswith('SIM-') for c in cards)
                logout = browser.post(path+'/messages', headers=csrf, json={'text':'Log me out'}).json()
                assert logout['input_tool'] == 'logout'
                assert browser.post('/auth/logout', headers=csrf).status_code == 200
                import hashlib
                assert browser.app.state.sessions.execute('SELECT 1 FROM sessions WHERE id=?', (hashlib.sha256(first_cookie.encode()).hexdigest(),)).fetchone() is None
                assert browser.get('/api/session').status_code == 401
                assert browser.get(path+'/inputs').status_code == 401
                assert browser.post('/auth/password-login', headers=origin, json={'username':'alex','password':'incorrect password'}).status_code == 401
                login = browser.post('/auth/password-login', headers=origin, json={'username':'ALEX','password':'synthetic password'})
                assert login.status_code == 200
                assert browser.cookies.get('card_session') != first_cookie
                csrf = {**origin, 'X-CSRF-Token':login.json()['csrf']}
                path = start()
                assert len(browser.post(path+'/messages', headers=csrf, json={'text':'Show my accounts'}).json()['data']['accounts']) == 1
                assert browser.post('/auth/register', headers=origin, json={'username':'alex','password':'synthetic password'}).status_code == 409
                browser.post('/auth/logout', headers=csrf)
                other = browser.post('/auth/register', headers=origin, json={'username':'sam','password':'another password'})
                csrf = {**origin, 'X-CSRF-Token':other.json()['csrf']}
                assert browser.get(path+'/inputs').status_code == 404
                other_claims = api.post('/banking/login', headers={**service_headers,'X-Banking-BFF':'1'}, json={'username':'sam','password':'another password'}).json()
                h = headers(audience='cards', permission='cards:write', client='mcp-service')
                h['X-Customer-Delegation'] = Security(config).issue_customer(other_claims)
                assert api.get('/banking/cards', headers=h).json()['cards'] == []
                assert api.post('/banking/cards', headers={**h,'Idempotency-Key':'stolen'}, json={'account_number':number}).status_code == 404
                # A forged/stale account list cannot override database ownership.
                other_claims['accounts'] = [number]
                h['X-Customer-Delegation'] = Security(config).issue_customer(other_claims)
                assert api.post('/cards', headers={**h,'Idempotency-Key':'bypass'}, json={'account_number':number,'user_name':'sam','given_name':'Sam'}).status_code == 403
                assert 'synthetic password' not in json.dumps(model.contexts)
                assert 'another password' not in json.dumps(model.contexts)
                # Neither legacy OIDC subjects nor a fabricated account list can claim a managed account.
                other_claims['sub'] = 'legacy-oidc-subject'
                h['X-Customer-Delegation'] = Security(config).issue_customer(other_claims)
                assert api.post('/cards', headers={**h,'Idempotency-Key':'legacy-bypass'}, json={'account_number':number,'user_name':'sam','given_name':'Sam'}).status_code == 403
    with sqlite3.connect(database) as db:
        encoded = db.execute('SELECT password_hash FROM customers WHERE username=?', ('alex',)).fetchone()[0]
        assert encoded.startswith('scrypt$') and 'synthetic password' not in encoded
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_account_and_card_retries_and_restart(tmp_path):
    from card_servicing.banking import BankingService, Credentials, AccountCreate
    path = str(tmp_path/'cards.db')
    store = SQLiteCardStore(path); store.initialize()
    service = BankingService(store)
    claims = service.register(Credentials(username='alex',password='synthetic password'))
    customer = {'sub':claims['sub'],'username':'alex'}
    body = AccountCreate(account_type='transaction',issue_card=True)
    first = service.create_account(customer,body,'one')
    store = SQLiteCardStore(path); store.initialize(); service = BankingService(store)
    assert service.create_account(customer,body,'one') == first
    assert len(service.cards(customer['sub'])) == 1
    assert service.login(Credentials(username='alex',password='synthetic password'))['accounts'] == [first['account_number']]
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as conflict:
        service.create_account(customer,AccountCreate(account_type='savings'),'one')
    assert conflict.value.status_code == 409


def test_interrupted_optional_card_is_completed_by_retry(tmp_path, monkeypatch):
    from card_servicing.banking import BankingService, Credentials, AccountCreate
    from card_servicing.ports import StorageUnavailable
    import pytest
    store = SQLiteCardStore(str(tmp_path/'cards.db')); store.initialize()
    service = BankingService(store)
    claims = service.register(Credentials(username='alex',password='synthetic password'))
    customer = {'sub':claims['sub'],'username':'alex'}
    original = store.issue_idempotent
    def offline(*args, **kwargs):
        raise StorageUnavailable('offline')
    monkeypatch.setattr(store,'issue_idempotent',offline)
    body = AccountCreate(account_type='savings',issue_card=True)
    with pytest.raises(StorageUnavailable):
        service.create_account(customer,body,'retry')
    assert len(service.accounts(customer['sub'])) == 1
    monkeypatch.setattr(store,'issue_idempotent',original)
    service.create_account(customer,body,'retry')
    assert len(service.accounts(customer['sub'])) == len(service.cards(customer['sub'])) == 1


def test_credentials_are_redacted_and_auth_attempts_are_limited(identity, tmp_path, monkeypatch):
    config, headers = identity
    config = {**config,'SESSION_DB':str(tmp_path/'session.db'),'PUBLIC_ORIGIN':'http://testserver'}
    async def service_headers(self):
        return headers()
    monkeypatch.setattr(Security,'headers',service_headers)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original_client(**{**kwargs,'transport':httpx.MockTransport(lambda request:httpx.Response(401,json={'detail':'Rejected'}))}))
    with TestClient(experience_app(config)) as client:
        origin = {'Origin':'http://testserver'}
        bad = client.post('/auth/register',headers=origin,json={'username':'x','password':'PRIVATE'})
        assert bad.status_code == 422 and 'PRIVATE' not in bad.text
        for _ in range(20):
            assert client.post('/auth/password-login',headers=origin,json={'username':'alex','password':'incorrect password'}).status_code == 401
        assert client.post('/auth/password-login',headers=origin,json={'username':'alex','password':'incorrect password'}).status_code == 429
