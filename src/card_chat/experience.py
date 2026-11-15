"""Browser session BFF. Only the typed agent contract crosses this boundary."""
import asyncio
from contextlib import asynccontextmanager
import base64
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from card_chat.config import settings
from card_chat.contracts import Confirmation, Message, Operation, Reply, StructuredInput, InputOptions
from fastapi.exceptions import RequestValidationError
from card_chat.security import Security, delegation, trace
from uuid import uuid4


def create_app(config=None):
    config = settings() if config is None else config
    security = Security(config)
    path = config.get('SESSION_DB', ':memory:')
    if path != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, data TEXT, expires REAL)')
    @asynccontextmanager
    async def lifespan(app):
        if not security.enabled:
            raise RuntimeError('Configure OIDC before starting Experience')
        try:
            yield
        finally:
            db.close()
    app = FastAPI(title='Card Experience API', version='1.0', lifespan=lifespan)
    app.state.sessions = db

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': 'Invalid request fields. Use the secure action form.'}, status_code=422)
    secure_cookie = config.get('COOKIE_SECURE', 'false').lower() == 'true'
    origin = config.get('PUBLIC_ORIGIN', 'http://localhost:8080')
    callback = origin + '/auth/callback'

    def store(key, value, ttl):
        db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
        db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)',
                   (hashlib.sha256(key.encode()).hexdigest(), json.dumps(value), time.time() + ttl))
        db.commit()

    def load(key, *, consume=False):
        digest = hashlib.sha256((key or '').encode()).hexdigest()
        row = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (digest, time.time())).fetchone()
        if consume:
            db.execute('DELETE FROM sessions WHERE id=?', (digest,))
            db.commit()
        return json.loads(row['data']) if row else None

    def session(request):
        value = load(request.cookies.get('card_session'))
        if not value or 'claims' not in value:
            raise HTTPException(401, 'Sign in required')
        if request.method != 'GET':
            if (request.headers.get('Origin') != origin or
                    not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), value['csrf'])):
                raise HTTPException(403, 'Invalid CSRF token or origin')
        return value

    @app.middleware('http')
    async def safe_responses(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/health')
    async def health():
        db.execute('SELECT 1')
        return {'status': 'ok'}

    @app.get('/auth/login')
    async def login():
        state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        store(state, {'verifier': verifier, 'nonce': nonce}, 300)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        url = config['OIDC_AUTHORIZE_URL'] + '?' + urlencode({
            'client_id': 'experience-browser', 'redirect_uri': callback, 'response_type': 'code',
            'scope': 'openid profile', 'state': state, 'nonce': nonce,
            'code_challenge': challenge, 'code_challenge_method': 'S256'})
        response = RedirectResponse(url)
        response.set_cookie('card_oidc', state, httponly=True, secure=secure_cookie,
                            samesite='lax', max_age=300, path='/auth')
        return response

    @app.get('/auth/callback')
    async def complete_login(request: Request, code: str, state: str):
        if not secrets.compare_digest(state, request.cookies.get('card_oidc', '')):
            raise HTTPException(401, 'Invalid sign-in state')
        pending = load(state, consume=True)
        if not pending:
            raise HTTPException(401, 'Sign-in expired')
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(config['OIDC_TOKEN_URL'], data={
                'grant_type': 'authorization_code', 'client_id': 'experience-browser',
                'client_secret': config['BROWSER_CLIENT_SECRET'], 'code': code,
                'redirect_uri': callback, 'code_verifier': pending['verifier']})
        if response.status_code != 200:
            raise HTTPException(401, 'Sign-in failed')
        claims = await asyncio.to_thread(security.verify, response.json()['id_token'], 'experience-browser')
        if not secrets.compare_digest(claims.get('nonce', ''), pending['nonce']):
            raise HTTPException(401, 'Invalid sign-in nonce')
        sid = secrets.token_urlsafe(32)
        store(sid, {'claims': claims, 'csrf': secrets.token_urlsafe(32)}, 3600)
        result = RedirectResponse('/')
        result.delete_cookie('card_oidc', path='/auth')
        result.set_cookie('card_session', sid, httponly=True, secure=secure_cookie,
                          samesite='lax', max_age=3600, path='/')
        return result

    @app.get('/api/session')
    async def current_session(request: Request):
        value = session(request)
        return {'name': value['claims']['preferred_username'], 'csrf': value['csrf']}

    @app.post('/auth/logout')
    async def logout(request: Request):
        session(request)
        load(request.cookies.get('card_session'), consume=True)
        result = JSONResponse({'message': 'Signed out locally'})
        result.delete_cookie('card_session', path='/')
        return result

    async def forward(request, path, body=None):
        value = session(request)
        dt = delegation.set(security.issue_customer(value['claims']))
        tt = trace.set(uuid4().hex)
        try:
            headers = await security.headers()
            for key in ('Prefer', 'Idempotency-Key'):
                if request.headers.get(key):
                    headers[key] = request.headers[key]
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.request(request.method, config['AGENT_URL'].rstrip('/') + path,
                                                json=body, headers=headers)
            if response.status_code >= 400:
                # Validation bodies can contain customer input: never forward them.
                return JSONResponse({'detail': 'Agent request was rejected or unavailable'}, status_code=response.status_code)
            content = response.json()
            if request.method == 'GET' and path.endswith('/inputs'):
                # Only the public form projection crosses the BFF boundary.
                return JSONResponse(InputOptions.model_validate(content).model_dump(exclude_none=True),
                                    headers={'X-Correlation-ID': headers['X-Correlation-ID']})
            if response.status_code == 202 or path.startswith('/operations/'):
                parsed = Operation.model_validate(content)
                parsed.status_url = '/api/operations/' + parsed.id
            else:
                parsed = Reply.model_validate(content)
            return JSONResponse(parsed.model_dump(), status_code=response.status_code,
                                headers={'X-Correlation-ID': headers['X-Correlation-ID'],
                                         **({'Location': parsed.status_url} if response.status_code == 202 else {})})
        except (httpx.RequestError, ValueError):
            raise HTTPException(503, 'Agent unavailable. Retry the same request key.') from None
        finally:
            delegation.reset(dt)
            trace.reset(tt)

    @app.post('/api/conversations', response_model=Reply)
    async def start(request: Request):
        return await forward(request, '/conversations')

    @app.post('/api/conversations/{cid}/messages', response_model=Reply, responses={202: {'model': Operation}})
    async def message(cid: str, body: Message, request: Request):
        check_id(cid)
        return await forward(request, f'/conversations/{cid}/messages', body.model_dump())

    @app.post('/api/conversations/{cid}/confirm', response_model=Reply, responses={202: {'model': Operation}})
    async def confirm(cid: str, body: Confirmation, request: Request):
        check_id(cid)
        return await forward(request, f'/conversations/{cid}/confirm', body.model_dump())

    @app.get('/api/operations/{oid}', response_model=Operation)
    async def status(oid: str, request: Request):
        check_id(oid)
        return await forward(request, '/operations/' + oid)

    @app.get('/api/conversations/{cid}/inputs', response_model=InputOptions)
    async def inputs(cid: str, request: Request):
        check_id(cid)
        return await forward(request, f'/conversations/{cid}/inputs')

    @app.post('/api/conversations/{cid}/inputs', response_model=Reply, responses={202: {'model': Operation}})
    async def supply_inputs(cid: str, body: StructuredInput, request: Request):
        check_id(cid)
        return await forward(request, f'/conversations/{cid}/inputs', body.model_dump())

    return app


def check_id(value):
    if len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
        raise HTTPException(404, 'Not found')


app = create_app()
