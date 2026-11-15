"""Reconcile only demo service scopes in an already imported local Keycloak realm."""
from pathlib import Path
import httpx
from dotenv import dotenv_values

config = dotenv_values(Path(__file__).resolve().parents[1] / '.env.stack')
with httpx.Client(base_url='http://localhost:8081', timeout=30) as client:
    response = client.post('/realms/master/protocol/openid-connect/token', data={
        'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin',
        'password': config['IDENTITY_ADMIN_PASSWORD']})
    response.raise_for_status()
    client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
    base = '/admin/realms/cards'
    scopes = {scope['name']: scope['id'] for scope in client.get(base + '/client-scopes').json()}
    for name, permission in [('experience-service', 'agent:invoke'), ('agent-service', 'tools:execute'),
                             ('mcp-service', 'cards:write')]:
        if permission not in scopes:
            result = client.post(base + '/client-scopes', json={'name': permission, 'protocol': 'openid-connect',
                'attributes': {'include.in.token.scope': 'true', 'display.on.consent.screen': 'false'}})
            result.raise_for_status()
            scopes = {scope['name']: scope['id'] for scope in client.get(base + '/client-scopes').json()}
        item = client.get(base + '/clients', params={'clientId': name}).json()[0]
        result = client.put(base + '/clients/' + item['id'] + '/default-client-scopes/' + scopes[permission])
        result.raise_for_status()
print('Demo destination scopes reconciled. No credentials changed.')
