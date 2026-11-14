import pytest
from fastapi.testclient import TestClient
from card_servicing.main import create_app

BODY = {'user_name': 'alex.smith', 'given_name': 'Alex', 'account_number': '0012345678'}

def test_create_and_normalized_retry():
    with TestClient(create_app()) as client:
        first = client.post('/cards', json=BODY, headers={'Idempotency-Key': 'card-001'})
        assert first.status_code == 201
        assert first.json()['card_number'].startswith('SIM-')
        assert first.json()['status'] == 'created'
        assert first.json()['account_number'] == '0012345678'
        assert first.headers['Cache-Control'] == 'no-store'
        retry = client.post('/cards', json={k:' '+v+' ' for k,v in BODY.items()}, headers={'Idempotency-Key': 'card-001'})
        assert retry.json() == first.json()
        assert retry.headers['Idempotency-Replayed'] == 'true'
        assert retry.headers['X-Trace-ID'] != first.headers['X-Trace-ID']

@pytest.mark.parametrize('change', [{'user_name':''}, {'given_name':'  '}, {'account_number':123}, {'account_number':'a'*65}, {'given_name':'A\x00'}, {'unexpected':'x'}])
def test_validation_no_mutation(change):
    app=create_app()
    with TestClient(app) as client:
        assert client.post('/cards', json={**BODY, **change}, headers={'Idempotency-Key':'test'}).status_code == 422
        assert app.state.card_store.list() == []

@pytest.mark.parametrize('key', [None, '', 'bad key', 'x'*129])
def test_required_valid_key(key):
    with TestClient(create_app()) as client:
        assert client.post('/cards', json=BODY, headers={} if key is None else {'Idempotency-Key':key}).status_code == 422

def test_missing_field_and_new_intent():
    with TestClient(create_app()) as client:
        assert client.post('/cards', json={}, headers={'Idempotency-Key':'one'}).status_code == 422
        a=client.post('/cards', json=BODY, headers={'Idempotency-Key':'one'}).json()
        b=client.post('/cards', json=BODY, headers={'Idempotency-Key':'two'}).json()
        assert a['card_number'] != b['card_number']
