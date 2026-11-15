"""Black-box OIDC + browser-contract smoke. Synthetic accounts, stub model only.

Run with the normal Compose stack healthy. No bearer tokens or cookies are printed.
"""
from html.parser import HTMLParser
from contextlib import closing
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx
from dotenv import dotenv_values

ORIGIN = 'http://localhost:8080'
BODY = {'given_name': 'Alex', 'user_name': 'alex.synthetic', 'account_number': '0012345678'}


class Form(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form' and attrs.get('id') == 'kc-form-login':
            self.action = attrs.get('action')
        if tag == 'input' and attrs.get('type') == 'hidden' and attrs.get('name'):
            self.fields[attrs['name']] = attrs.get('value', '')


def sign_in(username):
    client = httpx.Client(base_url=ORIGIN, follow_redirects=True, timeout=120)
    page = client.get('/auth/login')
    # Browsers treat localhost as a trustworthy context and send Keycloak's
    # Secure cookies there. Python CookieJar does not; emulate that local-only
    # browser behavior in this harness, never in application cookie settings.
    for cookie in client.cookies.jar:
        if cookie.domain in ('localhost', 'localhost.local'):
            cookie.secure = False
    form = Form()
    form.feed(page.text)
    assert form.action, 'OIDC login page not available'
    password = dotenv_values(Path(__file__).resolve().parents[1] / '.env.stack')['DEMO_PASSWORD']
    result = client.post(form.action, data={**form.fields, 'username': username, 'password': password})
    assert result.status_code == 200 and result.url.host == 'localhost', 'OIDC callback failed'
    session = client.get('/api/session')
    assert session.status_code == 200, 'Server-side session was not established'
    client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': session.json()['csrf']})
    return client


def operation(client, path, body, asynchronous=False, key=None):
    response = client.post(path, json=body, headers={'Idempotency-Key': key or uuid4().hex,
        **({'Prefer': 'respond-async'} if asynchronous else {})})
    assert response.status_code in (200, 202), f'Unexpected response: {response.status_code}'
    if response.status_code == 200:
        return response.json()
    job = response.json()
    assert response.headers['Location'] == job['status_url']
    for _ in range(240):
        job = client.get(job['status_url']).json()
        if job['status'] == 'succeeded':
            return job['result']
        assert job['status'] != 'failed', job.get('error')
        time.sleep(.25)
    raise AssertionError('Polling timed out')


def main():
    assert httpx.post(ORIGIN + '/api/conversations').status_code == 401
    with closing(sign_in('alex.synthetic')) as client, closing(sign_in('sam.synthetic')) as other:
        assert client.post('/api/conversations', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
        for asynchronous in (False, True):
            cid = operation(client, '/api/conversations', {})['id']
            path = f'/api/conversations/{cid}'
            assert other.post(path + '/messages', json={'text': 'cross-user'}).status_code == 404
            blocked = operation(client, path + '/messages', {'text': json.dumps(BODY)}, asynchronous)
            assert not blocked['review'] and blocked['usage']['model_calls'] == 0
            options = client.get(path + '/inputs').json()
            assert BODY['account_number'] not in json.dumps(options)
            ref = next(field['options'][0]['ref'] for field in options['tools'][0]['fields'] if field.get('options'))
            inputs = {'tool': 'create_card', 'fields': {'given_name': 'Alex'}, 'selections': {'account_number': ref}}
            review = operation(client, path + '/inputs', inputs, asynchronous)
            assert review['can_confirm'] and 'SIM-' not in review['message']
            assert BODY['account_number'] not in review['message']
            fresh = operation(client, path + '/inputs', inputs, asynchronous)
            assert client.post(path + '/confirm', json={'review': review['review']}).status_code == 409
            confirmation = {'review': fresh['review']}
            key = uuid4().hex
            result = operation(client, path + '/confirm', confirmation, asynchronous, key)
            assert 'SIM-' in result['message'], result['message']
            replay = operation(client, path + '/confirm', confirmation, asynchronous, key)
            assert replay['message'] == result['message']
            duplicate = operation(client, path + '/confirm', confirmation, asynchronous)
            assert duplicate['message'] == result['message']
            assert result['usage']['model_calls'] == 0 and result['usage']['completed_actions'] == 1
            print('PASS:', 'submit-and-poll' if asynchronous else 'request-response', 'OIDC -> Experience -> Agent -> remote MCP -> Cards')
        # A different signed-in customer cannot originate a card for Alex's account.
        cid = operation(other, '/api/conversations', {})['id']
        path = f'/api/conversations/{cid}'
        forbidden = other.post(path + '/inputs', json={'tool': 'create_card', 'fields': {'given_name': 'Sam'},
                                                       'selections': {'account_number': ref}})
        assert forbidden.status_code == 422
        print('PASS: anonymous, CSRF, stale review, cross-user reference, masked picker, duplicate confirmations; zero model calls')


if __name__ == '__main__':
    main()
