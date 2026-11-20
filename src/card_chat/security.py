"""Local OIDC resource validation and signed, bounded customer delegation.

Service access tokens are never forwarded to a different resource. The separate
customer assertion is signed by the BFF after OIDC login and is not an OAuth token.
"""
import asyncio
from contextvars import ContextVar
from pathlib import Path
import time
from uuid import uuid4

import httpx
import jwt
from fastapi import HTTPException
from starlette.responses import JSONResponse

delegation = ContextVar('delegation', default=None)
principal = ContextVar('principal', default=None)
trace = ContextVar('trace', default=None)
replacement_context = ContextVar('replacement_context', default=None)


class Security:
    def __init__(self, config):
        self.config = config
        self.enabled = bool(config.get('OIDC_ISSUER'))
        self.jwks = jwt.PyJWKClient(config['OIDC_JWKS_URL']) if self.enabled else None

    def verify(self, token, audience, permission=None, client=None):
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=['RS256'], audience=audience,
                                issuer=self.config['OIDC_ISSUER'],
                                options={'require': ['exp', 'iat', 'sub', 'iss', 'aud']})
            if client:
                allowed_clients = {client} if isinstance(client, str) else set(client)
                if claims.get('azp') not in allowed_clients:
                    raise HTTPException(403, 'Service identity not permitted')
            if permission:
                scopes = set(claims.get('scope', '').split())
                required_scopes = {permission} if isinstance(permission, str) else set(permission)
                if not required_scopes.intersection(scopes):
                    raise HTTPException(403, 'Permission required')
            return claims
        except (jwt.PyJWTError, ValueError, TypeError):
            raise HTTPException(401, 'Invalid credentials') from None

    def customer(self, token, audience):
        try:
            key = Path(self.config['DELEGATION_PUBLIC_KEY']).read_text()
            claims = jwt.decode(token, key, algorithms=['EdDSA'], audience=audience,
                              issuer='card-experience',
                              options={'require': ['exp', 'iat', 'sub', 'iss', 'aud', 'accounts', 'username']})
            if (not isinstance(claims['accounts'], list) or
                    not all(isinstance(value, str) for value in claims['accounts']) or
                    not isinstance(claims.get('permissions', []), list) or
                    not all(isinstance(value, str) for value in claims.get('permissions', [])) or
                    not isinstance(claims['username'], str) or not isinstance(claims['sub'], str)):
                raise ValueError('Invalid delegated facts')
            if 'cards' in claims and not isinstance(claims['cards'], list):
                raise ValueError('Invalid delegated facts')
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, OSError):
            raise HTTPException(401, 'Invalid customer delegation') from None

    def issue_customer(self, claims):
        now = int(time.time())
        username = claims.get('preferred_username') or claims.get('username') or ''
        return jwt.encode({'iss': 'card-experience', 'aud': ['agent', 'mcp', 'cards', 'fincrime'],
                           'sub': claims['sub'], 'username': username,
                           'accounts': claims.get('accounts', []),
                           'cards': claims.get('cards', []),
                           'iat': now,
                           'permissions': claims.get('permissions', self.config.get('CUSTOMER_TOOL_PERMISSIONS', 'tools:execute').split()),
                           'exp': now + 3600, 'jti': uuid4().hex},
                          Path(self.config['DELEGATION_PRIVATE_KEY']).read_text(), algorithm='EdDSA')

    async def service_token(self):
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(self.config['OIDC_TOKEN_URL'], data={
                'grant_type': 'client_credentials', 'client_id': self.config['SERVICE_CLIENT_ID'],
                'client_secret': self.config['SERVICE_CLIENT_SECRET']})
            response.raise_for_status()
            return response.json()['access_token']

    async def headers(self):
        headers = {'X-Correlation-ID': trace.get() or uuid4().hex}
        if self.enabled:
            headers['Authorization'] = 'Bearer ' + await self.service_token()
            if delegation.get():
                headers['X-Customer-Delegation'] = delegation.get()
        return headers


def protect(app, config, audience, permission, client, *, customer=True):
    security = Security(config)
    app.state.security = security

    @app.middleware('http')
    async def authenticate(request, call_next):
        supplied = request.headers.get('X-Correlation-ID', '')
        tid = supplied if len(supplied) == 32 and all(c in '0123456789abcdef' for c in supplied) else uuid4().hex
        t = trace.set(tid)
        p = principal.set(None)
        d = delegation.set(request.headers.get('X-Customer-Delegation'))
        try:
            if security.enabled and not request.url.path.startswith(('/health', '/.well-known/')):
                auth = request.headers.get('Authorization', '')
                if not auth.startswith('Bearer '):
                    raise HTTPException(401, 'Bearer credentials required')
                declaration_entry = audience == 'cards' and request.url.path == '/banking/replacement-context'
                approved_cards_execution = (audience == 'cards'
                    and request.url.path.endswith('/replace')
                    and request.headers.get('X-Approved-Case-Execution') == '1')
                bff_banking = audience == 'cards' and request.url.path.startswith('/banking/') and request.headers.get('X-Banking-BFF') == '1'
                await asyncio.to_thread(security.verify, auth[7:],
                    'agent' if bff_banking else ('mcp' if declaration_entry or approved_cards_execution else audience),
                    'agent:invoke' if bff_banking else ('tools:execute' if declaration_entry or approved_cards_execution else permission),
                    'experience-service' if bff_banking else ('agent-service' if declaration_entry or approved_cards_execution else client))
                public_entry = audience == 'agent' and request.url.path == '/entry/messages'
                fincrime_entry = audience == 'agent' and request.url.path.startswith('/fincrime/')
                approved_execution = (
                    request.headers.get('X-Approved-Case-Execution') == '1'
                    and ((audience == 'mcp' and request.url.path.startswith('/mcp'))
                         or (audience == 'cards' and request.url.path.endswith('/replace')))
                )
                credential_entry = bff_banking and request.url.path in ('/banking/register', '/banking/login')
                if request.url.path in ('/banking/register', '/banking/login') and not bff_banking:
                    raise HTTPException(403, 'Experience service required')
                if customer and not public_entry and not credential_entry and not fincrime_entry and not approved_execution:
                    principal.set(security.customer(delegation.get(), audience))
            response = await call_next(request)
        except HTTPException as exc:
            challenge = 'Bearer'
            if audience == 'mcp':
                resource = config.get('MCP_RESOURCE', 'http://mcp-server:8000/mcp')
                challenge += ' resource_metadata="' + resource.rsplit('/', 1)[0] + '/.well-known/oauth-protected-resource"'
            response = JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                    headers={'WWW-Authenticate': challenge})
        finally:
            trace.reset(t)
            principal.reset(p)
            delegation.reset(d)
        response.headers['X-Correlation-ID'] = tid
        response.headers['Cache-Control'] = 'no-store'
        return response

    return security
