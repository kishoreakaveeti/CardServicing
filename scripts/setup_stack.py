"""Generate ignored local demo credentials, Keycloak realm and delegation keys."""
import json
import os
from pathlib import Path
import secrets
from dotenv import dotenv_values
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
directory = Path(os.environ.get('IDENTITY_OUTPUT_DIR', ROOT / 'data' / 'identity'))
directory.mkdir(parents=True, exist_ok=True)
env = Path(os.environ.get('STACK_ENV_FILE', ROOT / '.env.stack'))
values = dict(dotenv_values(env)) if env.exists() else {name: secrets.token_urlsafe(32) for name in
          ['EXPERIENCE_SECRET', 'AGENT_SECRET', 'MCP_SECRET', 'BROWSER_SECRET',
           'IDENTITY_ADMIN_PASSWORD', 'DEMO_PASSWORD']}
for name in ('EXPERIENCE_SECRET', 'AGENT_SECRET', 'MCP_SECRET', 'BROWSER_SECRET',
             'IDENTITY_ADMIN_PASSWORD', 'DEMO_PASSWORD', 'KEYCLOAK_DB_PASSWORD', 'KEYCLOAK_DB_ROOT_PASSWORD'):
    values.setdefault(name, secrets.token_urlsafe(32))
original = {} if os.environ.get('IGNORE_LOCAL_ENV') == '1' else dotenv_values(ROOT / '.env')
# Keep original Mongo credentials when migrating an existing development setup.
values.setdefault('MONGO_USER', original.get('MONGO_USER') or 'card_servicing')
values.setdefault('MONGO_PASSWORD', original.get('MONGO_PASSWORD') or secrets.token_urlsafe(32))
values.setdefault('ACCOUNT_HASH_KEY', original.get('ACCOUNT_HASH_KEY') or secrets.token_urlsafe(32))

def mapper(name, kind, config):
    return {'name': name, 'protocol': 'openid-connect', 'protocolMapper': kind, 'config': config}

def audience(name):
    return mapper('audience', 'oidc-audience-mapper', {'included.custom.audience': name,
                  'access.token.claim': 'true', 'id.token.claim': 'false'})

def service(name, secret, target, permission):
    return {'clientId': name, 'secret': values[secret], 'enabled': True,
            'protocol': 'openid-connect', 'publicClient': False, 'serviceAccountsEnabled': True,
            'standardFlowEnabled': False, 'fullScopeAllowed': False,
            'defaultClientScopes': [permission], 'protocolMappers': [audience(target)]}

clients = [service('experience-service', 'EXPERIENCE_SECRET', 'agent', 'agent:invoke'),
           service('agent-service', 'AGENT_SECRET', 'mcp', 'tools:execute'),
           service('mcp-service', 'MCP_SECRET', 'cards', 'cards:write')]
clients[2]['defaultClientScopes'].append('tools:execute')
fincrime_audience = audience('fincrime')
fincrime_audience['name'] = 'fincrime-audience'
clients[2]['protocolMappers'].append(fincrime_audience)
clients.append({'clientId': 'experience-browser', 'secret': values['BROWSER_SECRET'],
    'enabled': True, 'protocol': 'openid-connect', 'publicClient': False,
    'standardFlowEnabled': True, 'directAccessGrantsEnabled': False,
    'redirectUris': [os.environ.get('PUBLIC_ORIGIN', 'http://localhost:8080').rstrip('/') + '/auth/callback'],
    'defaultClientScopes': ['profile'],
    'attributes': {'pkce.code.challenge.method': 'S256'},
    'protocolMappers': [mapper('username', 'oidc-usermodel-property-mapper',
        {'user.attribute': 'username', 'claim.name': 'preferred_username', 'jsonType.label': 'String',
         'id.token.claim': 'true', 'access.token.claim': 'false'}),
        mapper('accounts', 'oidc-usermodel-attribute-mapper',
        {'user.attribute': 'accounts', 'claim.name': 'accounts', 'jsonType.label': 'String',
         'multivalued': 'true', 'id.token.claim': 'true', 'access.token.claim': 'false'})]})
realm = {'realm': 'cards', 'enabled': True, 'sslRequired': os.environ.get('KEYCLOAK_SSL_REQUIRED', 'none'), 'registrationAllowed': False,
         'accessTokenLifespan': 300, 'clients': clients,
         'clientScopes': [{'name': permission, 'protocol': 'openid-connect',
                           'attributes': {'include.in.token.scope': 'true', 'display.on.consent.screen': 'false'}}
                          for permission in ['agent:invoke', 'tools:execute', 'cards:write', 'profile']],
         'users': [{'username': name, 'enabled': True, 'firstName': first, 'lastName': 'Synthetic',
                    'email': name + '@example.invalid', 'emailVerified': True,
                    'attributes': {'accounts': [account]},
                    'credentials': [{'type': 'password', 'value': values['DEMO_PASSWORD'], 'temporary': False}]}
                   for name, first, account in [('alex.synthetic', 'Alex', '0012345678'),
                                                 ('sam.synthetic', 'Sam', '0098765432')]]}
if not (directory / 'delegation-private.pem').exists():
    private = Ed25519PrivateKey.generate()
    (directory / 'delegation-private.pem').write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (directory / 'delegation-public.pem').write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
(directory / 'realm.json').write_text(json.dumps(realm, indent=2), encoding='utf-8')
env.write_text('\n'.join(f'{key}={value}' for key, value in values.items()) + '\n', encoding='utf-8')
if os.name != 'nt':
    env.chmod(0o600)

print('Prepared .env.stack and data/identity, preserving existing credentials and keys. Secrets were not printed.')
