import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse

from card_chat.config import settings
from card_chat.gateway import connect
from card_chat.provider import make_model
from card_chat.agent import CardServicingAgent
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.contracts import Message, Confirmation, Reply, Operation
from card_chat.durable import Journal
from card_chat.security import protect, principal, delegation, trace
import json
from dataclasses import asdict
import hashlib
import logging
from card_chat.contracts import StructuredInput, InputOptions
from fastapi.exceptions import RequestValidationError
from card_chat.model_context import (PRIVATE_PASTE, PRIVATE_NOTICE, REFERENCE, sanitise, build_context,
                                     tools_for_model, ambiguous_old_reference)
from card_chat.private_inputs import eligible, bind, resolve, authorise, form_options, mask

logger = logging.getLogger(__name__)


@dataclass
class Conversation:
    history: list = field(default_factory=list)
    draft: dict = field(default_factory=dict)
    proposal: ProposedToolCall | None = None
    review: str | None = None
    submission: dict | None = None
    bindings: dict = field(default_factory=dict)
    usage: dict = field(default_factory=lambda: {key: 0 for key in ('model_calls', 'input_tokens',
        'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'completed_actions')})
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def create_app(model=None, gateway=None, *, config=None):
    config = settings() if config is None else config
    journal = Journal(config.get('AGENT_DB', ':memory:'))
    tasks = set()
    worker_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if model is None and gateway is None and not config.get('OIDC_ISSUER') and config.get('ALLOW_INSECURE_LOCAL') != '1':
            raise RuntimeError('Configure OIDC before starting Agent; isolated local tests must opt into ALLOW_INSECURE_LOCAL=1')
        app.state.model = model or make_model(config)
        app.state.agent = CardServicingAgent(app.state.model)
        try:
            if gateway is not None:
                app.state.gateway = gateway
                recover()
                yield
            else:
                async with connect(config) as connected:
                    app.state.gateway = connected
                    recover()
                    yield
        finally:
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.state.model.close()
            journal.close()

    app = FastAPI(title='Card Agent API', version='1.0', lifespan=lifespan)
    protect(app, config, 'agent', 'agent:invoke', 'experience-service')
    app.state.journal = journal

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': 'Invalid request fields. Use the secure action form.'}, status_code=422)

    def owner():
        return (principal.get() or {}).get('sub', 'isolated-test')

    def caller():
        if app.state.security.enabled:
            return principal.get() or {}
        return config.get('LOCAL_CUSTOMER', {'sub': owner(), 'permissions': ['tools:execute']})

    def permitted():
        names = config.get('MODEL_ALLOWED_TOOLS')
        return eligible(app.state.gateway.catalogue, caller(), names.split(',') if names is not None else None)

    def private_values(c):
        values = [item['value'] for item in c.bindings.values()]
        # Old durable drafts/proposals are not trusted model context either.
        for tool, data in ((c.draft.get('tool'), c.draft.get('arguments', {})),
                           (c.proposal.name if c.proposal else None, c.proposal.arguments if c.proposal else {})):
            spec = app.state.gateway.catalogue.get(tool)
            safe_fields = spec.entry.context_fields if spec else []
            values.extend(value for key, value in data.items() if key not in safe_fields
                          and isinstance(value, (str, int)) and not REFERENCE.fullmatch(str(value)))
        values.extend(caller().get('accounts', []))
        values.append(caller().get('username'))
        return values

    def save(cid, c):
        data = {k: v for k, v in vars(c).items() if k != 'lock'}
        if c.proposal:
            data['proposal'] = asdict(c.proposal)
        journal.save(cid, owner(), data)

    @app.middleware('http')
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def get(cid):
        data = journal.load(cid, owner())
        if data is None:
            raise HTTPException(404, 'Conversation not found. Start a new conversation.')
        if data['proposal']:
            data['proposal'] = ProposedToolCall(**data['proposal'])
        return Conversation(**data)

    def reply(c, text, *, language=False):
        c.history.append({'role': 'assistant', 'content': text if language else 'Action workflow updated.',
                          'kind': 'model' if language else 'workflow'})
        spec = app.state.gateway.catalogue[c.proposal.name] if c.proposal else None
        can_confirm = bool(c.review) and not (c.submission and c.submission['result']) and not (c.submission and c.submission['attempted']
                                             and not c.submission['result'] and not spec.safe_retry)
        return {'message': text, 'review': c.review, 'can_confirm': can_confirm,
                'confirmation_label': (spec.entry.confirmation_label or spec.entry.label) if spec else None,
                'usage': dict(c.usage)}

    async def execute(cid, c):
        proposal = c.proposal
        spec = app.state.gateway.catalogue[proposal.name]
        if proposal.name not in permitted():
            raise HTTPException(403, 'Action is not permitted')
        authorise(spec, proposal.arguments, caller())
        if c.submission is None:
            c.submission = {'tool': proposal.name, 'arguments': spec.validate(proposal.arguments),
                            'key': uuid4().hex if spec.entry.idempotency_header else None,
                            'result': None, 'attempted': False}
        submission = c.submission
        if submission['result'] is not None:
            return reply(c, submission['result'])
        if submission['attempted'] and not spec.safe_retry:
            return reply(c, 'This action cannot be safely retried. Check its status before starting another request.')
        submission['attempted'] = True
        # Durable exact arguments and key precede the first write and every retry.
        save(cid, c)
        suffix = ('Click the confirmation button to retry the same submission safely.' if spec.safe_retry
                  else 'The outcome may be uncertain. This action cannot be safely retried; check its status first.')
        try:
            # Execute the reviewed snapshot directly. Confirmation makes no model call.
            result = await app.state.gateway.execute(submission['tool'], submission['arguments'], submission['key'])
            if result.get('ok') is not True:
                error = result.get('error', 'Execution failed.')
                if not isinstance(error, str) or sanitise(error, private_values(c)) != error:
                    error = 'Execution failed.'
                return reply(c, error + ' ' + suffix)
            data = result.get('result')
            if not isinstance(data, dict):
                raise ValueError('Invalid structured API result')
            fields = '; '.join(f'{label}: {mask(data[key]) if ("account" in key or "card_number" in key) and not str(data[key]).startswith("SIM-") else data[key]}'
                               for key, label in spec.entry.result_labels.items() if key in data)
            text = f'{spec.entry.label} succeeded.' + (' ' + fields if fields else '')
            submission['result'] = text
            c.usage['completed_actions'] = c.usage.get('completed_actions', 0) + 1
            logger.warning('workflow_usage %s', json.dumps({'tool': spec.name, **c.usage}, sort_keys=True))
            return reply(c, text)
        except Exception:
            return reply(c, 'Could not complete the action. ' + suffix)

    @app.get('/health')
    async def health():
        journal.db.execute('SELECT 1')
        return {'status': 'ok'}

    @app.get('/')
    async def index():
        return FileResponse(Path(__file__).with_name('index.html'))

    @app.post('/conversations', response_model=Reply)
    async def start():
        cid = uuid4().hex
        c = Conversation()
        result = {'id': cid, **reply(c, 'Hi! What would you like help with?')}
        save(cid, c)
        return result

    async def message(cid: str, body: Message, c):
        async with c.lock:
            if c.submission:
                return reply(c, 'This submission is fixed. Use its confirmation button for a supported retry, or start a new conversation.')
            # Every new turn invalidates any previous review before interpreting updates.
            c.review, c.proposal = None, None
            safe_text = sanitise(body.text, private_values(c))
            c.history.append({'role': 'user', 'content': safe_text, 'kind': 'chat'})
            if safe_text == PRIVATE_PASTE or safe_text != body.text:
                return reply(c, PRIVATE_NOTICE)
            if ambiguous_old_reference(safe_text):
                return reply(c, 'Please select the intended account or details again in the secure form; I cannot safely infer an older selection.')
            catalogue = permitted()
            if not catalogue:
                return reply(c, 'No eligible actions are available for this caller.')
            verified_draft = {}
            if c.draft.get('tool') in catalogue:
                try:
                    draft_spec = catalogue[c.draft['tool']]
                    draft_values = resolve(draft_spec, c.draft.get('arguments', {}), c.bindings, cid, owner())
                    authorise(draft_spec, draft_values, caller())
                    verified_draft = c.draft
                except (ValueError, HTTPException):
                    pass  # Unbound, legacy or revoked facts never enter model context.
            history, draft, tools = build_context([turn for turn in c.history if turn.get('kind') in ('chat', 'model')],
                verified_draft, tools_for_model(catalogue), private_values=private_values(c))
            c.usage['model_calls'] = c.usage.get('model_calls', 0) + 1
            try:
                turn = await app.state.agent.interpret_turn(history, draft, tools)
            except Exception:
                return reply(c, 'The language service is unavailable or proposed an invalid action. Please try again.')
            finally:
                for key, value in getattr(app.state.model, 'last_usage', {}).items():
                    if key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'):
                        c.usage[key] = c.usage.get(key, 0) + value
            if isinstance(turn, CustomerReply):
                return reply(c, sanitise(turn.text, private_values(c)), language=True)
            spec = app.state.gateway.catalogue[turn.name]
            proposal_arguments = {**(verified_draft.get('arguments', {}) if verified_draft.get('tool') == turn.name else {}),
                                  **turn.arguments}
            try:
                partial = resolve(spec, proposal_arguments, c.bindings, cid, owner())
                authorise(spec, partial, caller())
                missing = spec.missing(partial)
                if missing:
                    c.draft = {'tool': turn.name, 'arguments': proposal_arguments}
                    name = missing[0]
                    label = spec.entry.field_labels.get(name, spec.input_schema['properties'][name].get('title', name))
                    return reply(c, f'Please provide {label} in the secure action form.')
                arguments = spec.validate(partial)
            except ValueError:
                return reply(c, 'Please supply private details using the secure action form and use its current selection.')
            c.draft = {'tool': turn.name, 'arguments': proposal_arguments}
            c.proposal = ProposedToolCall(turn.name, arguments)
            if spec.entry.confirmation == 'none':
                response = await execute(cid, c)
                c.proposal, c.submission, c.review, c.draft = None, None, None, {}
                response.update(review=None, can_confirm=False, confirmation_label=None)
                return response
            c.review = uuid4().hex
            details = '; '.join(f'{label}: {value}' for label, value in spec.masked(arguments).items())
            return reply(c, f'Please review: {spec.entry.label}. {details}. Confirm with the button or tell me what to change.')

    @app.get('/conversations/{cid}/inputs', response_model=InputOptions, response_model_exclude_none=True)
    async def input_options(cid: str):
        async with worker_lock:
            c = get(cid)
            result = form_options(permitted(), caller(), c.bindings, cid, owner())
            save(cid, c)
            return result

    async def structured(cid, body, c):
        if c.submission:
            return reply(c, 'This submission is fixed. Retry it safely or start a new conversation.')
        c.review, c.proposal = None, None
        # An accepted correction invalidates the previous review even when its
        # replacement details fail validation. Do not persist unvalidated input.
        save(cid, c)
        spec = permitted().get(body.tool)
        if spec is None:
            raise HTTPException(403, 'Action is not permitted')
        references = dict(c.draft.get('arguments', {})) if c.draft.get('tool') == body.tool else {}
        if set(body.fields) & set(spec.entry.trusted_fields) or set(body.fields) & set(body.selections):
            raise HTTPException(422, 'Use a trusted selection for protected fields')
        try:
            for key, value in body.fields.items():
                if key not in spec.input_schema['properties']:
                    raise ValueError('Unknown input')
                if key in spec.entry.context_fields:
                    if isinstance(value, str) and sanitise(value) != value:
                        raise ValueError('Private text is not model-visible')
                    references[key] = value
                else:
                    references[key] = bind(c.bindings, cid, owner(), body.tool, key, value)
            for key, ref in body.selections.items():
                # A caller can only select an existing binding; cannot create an identifier.
                if key in spec.entry.context_fields:
                    raise ValueError('Use a field, not a selection, for public text')
                resolve(spec, {key: ref}, c.bindings, cid, owner())
                references[key] = ref
            for key, source in spec.entry.trusted_fields.items():
                if source == 'username':
                    if not caller().get('username'):
                        raise HTTPException(403, 'Verified customer identity required')
                    references[key] = bind(c.bindings, cid, owner(), body.tool, key, caller()['username'])
            values = resolve(spec, references, c.bindings, cid, owner())
            authorise(spec, values, caller())
            missing = spec.missing(values)
            c.draft = {'tool': body.tool, 'arguments': references}
            if missing:
                return reply(c, 'Please complete the required fields in the secure action form.')
            arguments = spec.validate(values)
        except ValueError:
            raise HTTPException(422, 'Structured details or selections are invalid') from None
        c.proposal = ProposedToolCall(body.tool, arguments)
        if spec.entry.confirmation == 'none':
            result = await execute(cid, c)
            c.proposal, c.submission, c.review, c.draft = None, None, None, {}
            result.update(review=None, can_confirm=False, confirmation_label=None)
            return result
        c.review = uuid4().hex
        details = '; '.join(f'{label}: {value}' for label, value in spec.masked(arguments).items())
        return reply(c, f'Please review: {spec.entry.label}. {details}. Confirm with the button or update the secure form.')

    async def confirm(cid: str, body: Confirmation, c):
        async with c.lock:
            if not c.review or body.review != c.review or c.proposal is None:
                raise HTTPException(409, 'Review changed. Read the latest review before confirming.')
            return await execute(cid, c)

    async def run_operation(oid):
        # A single worker serializes state and jobs, including competing HTTP calls.
        async with worker_lock:
            row = journal.db.execute('SELECT * FROM operations WHERE id=?', (oid,)).fetchone()
            if row['status'] not in ('pending', 'running'):
                return
            pt = principal.set({'sub': row['owner']})
            dt = delegation.set(row['delegation'])
            tt = trace.set(row['trace'])
            journal.db.execute("UPDATE operations SET status='running' WHERE id=?", (oid,))
            journal.db.commit()
            try:
                # Reload auth from the persisted signed assertion; expired jobs fail closed.
                if app.state.security.enabled:
                    claims = app.state.security.customer(row['delegation'], 'agent')
                    if claims['sub'] != row['owner']:
                        raise HTTPException(403, 'Customer mismatch')
                    principal.set(claims)
                body = json.loads(row['body'])
                c = get(row['cid'])
                if row['kind'] == 'messages':
                    result = await message(row['cid'], Message(**body), c)
                elif row['kind'] == 'inputs':
                    result = await structured(row['cid'], StructuredInput(**body), c)
                else:
                    result = await confirm(row['cid'], Confirmation(**body), c)
                # State and operation result commit atomically. execute() already saved write intent.
                if c is not None:
                    data = {k: v for k, v in vars(c).items() if k != 'lock'}
                    if c.proposal:
                        data['proposal'] = asdict(c.proposal)
                    journal.db.execute('UPDATE conversations SET data=? WHERE id=? AND owner=?',
                                       (json.dumps(data), row['cid'], row['owner']))
                journal.db.execute("UPDATE operations SET status='succeeded',result=? WHERE id=?",
                                   (json.dumps(result), oid))
                journal.db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = exc.detail if isinstance(exc, HTTPException) else 'Operation failed; retry with a new request key.'
                code = exc.status_code if isinstance(exc, HTTPException) else 503
                journal.db.execute("UPDATE operations SET status='failed',error=? WHERE id=?",
                                   (json.dumps({'detail': error, 'status_code': code}), oid))
                journal.db.commit()
            finally:
                principal.reset(pt)
                delegation.reset(dt)
                trace.reset(tt)

    def schedule(oid):
        task = asyncio.create_task(run_operation(oid))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    def recover():
        for row in journal.db.execute("SELECT id FROM operations WHERE status='pending'").fetchall():
            schedule(row['id'])

    def operation(oid):
        row = journal.db.execute('SELECT * FROM operations WHERE id=? AND owner=?', (oid, owner())).fetchone()
        if row is None:
            raise HTTPException(404, 'Operation not found')
        return {'id': oid, 'status_url': '/operations/' + oid, 'status': row['status'],
                'result': json.loads(row['result']) if row['result'] else None,
                'error': json.loads(row['error'])['detail'] if row['error'] else None}

    @app.get('/operations/{oid}', response_model=Operation)
    async def status(oid: str):
        return operation(oid)

    async def submit(cid, body, kind, request):
        get(cid)  # Authorize before accepting or returning a deduplicated operation.
        key = request.headers.get('Idempotency-Key') or uuid4().hex
        if len(key) > 128:
            raise HTTPException(422, 'Request key too long')
        original = json.dumps(body.model_dump(), sort_keys=True)
        if kind == 'messages':
            c = get(cid)
            safe = sanitise(body.text, private_values(c))
            body = Message(text=safe if safe == body.text else PRIVATE_PASTE)
        encoded = json.dumps(body.model_dump(), sort_keys=True)
        # Rejected private pastes are equivalent safe notices, not stored secrets
        # or dictionary-attackable hashes of PINs/credentials.
        request_hash = hashlib.sha256(encoded.encode()).hexdigest()
        old = journal.db.execute('SELECT * FROM operations WHERE owner=? AND cid=? AND request_key=?',
                                 (owner(), cid, key)).fetchone()
        if old and (old['kind'] != kind or (old['request_hash'] or hashlib.sha256(old['body'].encode()).hexdigest()) != request_hash):
            raise HTTPException(409, 'Request key already used for a different operation')
        oid = old['id'] if old else uuid4().hex
        if not old:
            journal.db.execute('INSERT INTO operations(id,owner,cid,kind,request_key,body,delegation,trace,status,request_hash) '
                               "VALUES(?,?,?,?,?,?,?,?,'pending',?)",
                               (oid, owner(), cid, kind, key, encoded, delegation.get(), trace.get(), request_hash))
            journal.db.commit()  # 202 can only follow durable acceptance.
        if request.headers.get('Prefer') == 'respond-async':
            if not old:
                schedule(oid)
            return JSONResponse(operation(oid), status_code=202, headers={'Location': '/operations/' + oid})
        if not old or old['status'] in ('pending', 'running'):
            await schedule(oid)
        result = operation(oid)
        if result['status'] == 'failed':
            row = journal.db.execute('SELECT error FROM operations WHERE id=?', (oid,)).fetchone()
            error = json.loads(row['error'])
            raise HTTPException(error['status_code'], error['detail'])
        return Reply(**result['result']).model_dump(exclude={'id'})

    @app.post('/conversations/{cid}/messages', response_model=Reply, responses={202: {'model': Operation}})
    async def submit_message(cid: str, body: Message, request: Request):
        return await submit(cid, body, 'messages', request)

    @app.post('/conversations/{cid}/confirm', response_model=Reply, responses={202: {'model': Operation}})
    async def submit_confirmation(cid: str, body: Confirmation, request: Request):
        return await submit(cid, body, 'confirm', request)

    @app.post('/conversations/{cid}/inputs', response_model=Reply, responses={202: {'model': Operation}})
    async def submit_inputs(cid: str, body: StructuredInput, request: Request):
        return await submit(cid, body, 'inputs', request)

    return app


app = create_app()
