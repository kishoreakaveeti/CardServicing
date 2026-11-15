"""Verify a fresh generated realm, then remove only that temporary test realm."""
import base64
import hashlib
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from dotenv import dotenv_values
from smoke_stack import Form

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / '.env.stack')
realm = json.loads((root / 'data/identity/realm.json').read_text())
name = 'cards-import-' + secrets.token_hex(4)
realm['realm'] = name
with httpx.Client(base_url='http://localhost:8081', timeout=30) as admin:
    response = admin.post('/realms/master/protocol/openid-connect/token', data={
        'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin',
        'password': config['IDENTITY_ADMIN_PASSWORD']})
    response.raise_for_status()
    admin.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
    response = admin.post('/admin/realms', json=realm)
    response.raise_for_status()
    try:
        with httpx.Client(base_url='http://localhost:8081', timeout=30) as client:
            base = '/realms/' + name + '/protocol/openid-connect'
            for cid, secret, audience, scope in [('experience-service','EXPERIENCE_SECRET','agent','agent:invoke'),
                ('agent-service','AGENT_SECRET','mcp','tools:execute'),('mcp-service','MCP_SECRET','cards','cards:write')]:
                result = client.post(base + '/token', data={'grant_type':'client_credentials',
                    'client_id':cid,'client_secret':config[secret]})
                result.raise_for_status()
                jwks = jwt.PyJWKClient('http://localhost:8081' + base + '/certs')
                token = result.json()['access_token']
                claims = jwt.decode(token, jwks.get_signing_key_from_jwt(token).key, algorithms=['RS256'],
                    issuer='http://localhost:8081/realms/' + name, audience=audience)
                assert scope in claims['scope'].split()
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            page = client.get(base + '/auth', params={'client_id':'experience-browser','response_type':'code',
                'redirect_uri':'http://localhost:8080/auth/callback','scope':'openid profile',
                'code_challenge':challenge,'code_challenge_method':'S256','nonce':'import-test'})
            form = Form(); form.feed(page.text)
            assert form.action, 'Fresh realm OIDC authorization failed'
            for cookie in client.cookies.jar:
                cookie.secure = False  # Match browser localhost handling, only in this test client.
            response = client.post(form.action, data={**form.fields,'username':'alex.synthetic','password':config['DEMO_PASSWORD']})
            assert response.status_code == 302, 'Fresh realm login failed'
            code = parse_qs(urlparse(response.headers['Location']).query)['code'][0]
            response = client.post(base + '/token', data={'grant_type':'authorization_code','client_id':'experience-browser',
                'client_secret':config['BROWSER_SECRET'],'redirect_uri':'http://localhost:8080/auth/callback',
                'code':code,'code_verifier':verifier})
            response.raise_for_status()
            token = response.json()['id_token']
            claims = jwt.decode(token, jwks.get_signing_key_from_jwt(token).key, algorithms=['RS256'],
                issuer='http://localhost:8081/realms/' + name,audience='experience-browser')
            assert claims['preferred_username'] == 'alex.synthetic' and claims['accounts'] == ['0012345678']
        print('PASS: fresh realm import, all destination scopes, OIDC PKCE login and account claims')
    finally:
        admin.delete('/admin/realms/' + name).raise_for_status()
