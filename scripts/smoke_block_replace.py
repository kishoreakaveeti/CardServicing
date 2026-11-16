"""Exercise one reviewed block/replacement plan with a fresh synthetic customer.

Requires the local Docker stack. Never uses an existing customer's cards and
never prints credentials, cookies, account numbers or card identifiers.
"""
from uuid import uuid4
import secrets
import httpx


def main():
    origin = 'http://localhost:8080'
    with httpx.Client(base_url=origin, timeout=120, headers={'Origin': origin}) as client:
        response = client.post('/auth/register', json={
            'username': 'workflow-smoke-' + uuid4().hex[:12], 'password': secrets.token_urlsafe(24)})
        assert response.status_code == 201, 'Synthetic registration failed'
        client.headers['X-CSRF-Token'] = response.json()['csrf']

        def post(path, body, key=None):
            response = client.post(path, json=body, headers={'Idempotency-Key': key or uuid4().hex})
            assert response.status_code == 200, f'Workflow request failed: HTTP {response.status_code}'
            return response.json()

        cid = post('/api/conversations', {})['id']
        path = f'/api/conversations/{cid}'
        review = post(path + '/inputs', {'tool': 'create_account',
                      'fields': {'account_type': 'transaction', 'issue_card': True}})
        created = post(path + '/confirm', {'review': review['review']})
        assert 'succeeded' in created['message'].lower(), 'Synthetic account setup failed'

        cid = post('/api/conversations', {})['id']
        path = f'/api/conversations/{cid}'
        proposed = post(path + '/messages', {'text': 'Please block my card and reissue a new one'})
        assert proposed.get('input_tool') == 'block_card', 'Compound request did not prepare blocking'
        options = client.get(path + '/inputs').json()['tools']
        tool = next(t for t in options if t['name'] == 'block_card')
        selection = next(f for f in tool['fields'] if f['name'] == 'card_id')['options'][0]['ref']
        review = post(path + '/inputs', {'tool': 'block_card', 'fields': {'reason': 'lost'},
                      'selections': {'card_id': selection}})
        assert review['confirmation_label'] == 'Block and replace this card', 'Combined review missing'
        key = uuid4().hex
        completed = post(path + '/confirm', {'review': review['review']}, key)
        assert not completed['can_confirm'], 'A second action click was required'
        assert completed['usage']['completed_actions'] == 2, 'Both banking writes did not succeed'
        assert post(path + '/confirm', {'review': review['review']}, key) == completed, 'Replay changed'
        options = client.get(path + '/inputs').json()['tools']
        for action in ('block_card', 'replace_card'):
            tool = next(t for t in options if t['name'] == action)
            cards = next(f for f in tool['fields'] if f['name'] == 'card_id')['options']
            assert len(cards) == 1, 'Expected one blocked original and one active replacement'
    print('PASS: authenticated block + replacement, one combined confirmation, actual card states and idempotent replay')


if __name__ == '__main__':
    main()
