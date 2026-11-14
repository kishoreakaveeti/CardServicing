import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from card_chat.config import settings
from card_chat.gateway import connect
from card_chat.provider import make_model
from card_chat.agent import CardServicingAgent
from card_chat.turns import CustomerReply, ProposedToolCall


@dataclass
class Conversation:
    history: list = field(default_factory=list)
    draft: dict = field(default_factory=dict)
    proposal: ProposedToolCall | None = None
    review: str | None = None
    submission: dict | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=4000)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    review: str


def create_app(model=None, gateway=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.model = model or make_model(settings())
        app.state.agent = CardServicingAgent(app.state.model)
        try:
            if gateway is not None:
                app.state.gateway = gateway
                yield
            else:
                async with connect() as connected:
                    app.state.gateway = connected
                    yield
        finally:
            await app.state.model.close()

    app = FastAPI(title='Card chat demo', lifespan=lifespan)
    conversations = {}

    @app.middleware('http')
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def get(cid):
        if cid not in conversations:
            raise HTTPException(404, 'Conversation not found. Start a new conversation.')
        return conversations[cid]

    def reply(c, text):
        c.history.append({'role': 'assistant', 'content': text})
        spec = app.state.gateway.catalogue[c.proposal.name] if c.proposal else None
        can_confirm = bool(c.review) and not (c.submission and c.submission['attempted']
                                             and not c.submission['result'] and not spec.safe_retry)
        return {'message': text, 'review': c.review, 'can_confirm': can_confirm,
                'confirmation_label': (spec.entry.confirmation_label or spec.entry.label) if spec else None}

    async def execute(c):
        proposal = c.proposal
        spec = app.state.gateway.catalogue[proposal.name]
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
        suffix = ('Click the confirmation button to retry the same submission safely.' if spec.safe_retry
                  else 'The outcome may be uncertain. This action cannot be safely retried; check its status first.')
        try:
            # Execute the reviewed snapshot directly. Confirmation makes no model call.
            result = await app.state.gateway.execute(submission['tool'], submission['arguments'], submission['key'])
            if result.get('ok') is not True:
                return reply(c, result.get('error', 'Execution failed.') + ' ' + suffix)
            data = result.get('result')
            if not isinstance(data, dict):
                raise ValueError('Invalid structured API result')
            fields = '; '.join(f'{label}: {data[key]}' for key, label in spec.entry.result_labels.items() if key in data)
            text = f'{spec.entry.label} succeeded.' + (' ' + fields if fields else '')
            submission['result'] = text
            return reply(c, text)
        except Exception:
            return reply(c, 'Could not complete the action. ' + suffix)

    @app.get('/')
    async def index():
        return FileResponse(Path(__file__).with_name('index.html'))

    @app.post('/conversations')
    async def start():
        cid = uuid4().hex
        c = conversations[cid] = Conversation()
        return {'id': cid, **reply(c, 'Hi! What would you like help with?')}

    @app.post('/conversations/{cid}/messages')
    async def message(cid: str, body: Message):
        c = get(cid)
        async with c.lock:
            if c.submission:
                return reply(c, 'This submission is fixed. Use its confirmation button for a supported retry, or start a new conversation.')
            c.history.append({'role': 'user', 'content': body.text})
            # Every new turn invalidates any previous review before interpreting updates.
            c.review, c.proposal = None, None
            try:
                turn = await app.state.agent.interpret_turn(c.history, c.draft, app.state.gateway.tools)
            except Exception:
                return reply(c, 'The language service is unavailable or proposed an invalid action. Please try again.')
            if isinstance(turn, CustomerReply):
                return reply(c, turn.text)
            spec = app.state.gateway.catalogue[turn.name]
            try:
                partial = spec.validate(turn.arguments, partial=True)
                missing = spec.missing(partial)
                if missing:
                    c.draft = {'tool': turn.name, 'arguments': partial}
                    name = missing[0]
                    label = spec.entry.field_labels.get(name, spec.input_schema['properties'][name].get('title', name))
                    return reply(c, f'Please provide {label}.')
                arguments = spec.validate(partial)
            except ValueError:
                return reply(c, 'Those details do not match the action requirements. Please correct them; identifiers must remain strings.')
            c.draft = {'tool': turn.name, 'arguments': arguments}
            c.proposal = ProposedToolCall(turn.name, arguments)
            if spec.entry.confirmation == 'none':
                response = await execute(c)
                c.proposal, c.submission, c.review, c.draft = None, None, None, {}
                response.update(review=None, can_confirm=False, confirmation_label=None)
                return response
            c.review = uuid4().hex
            details = '; '.join(f'{label}: {value}' for label, value in spec.masked(arguments).items())
            return reply(c, f'Please review: {spec.entry.label}. {details}. Confirm with the button or tell me what to change.')

    @app.post('/conversations/{cid}/confirm')
    async def confirm(cid: str, body: Confirmation):
        c = get(cid)
        async with c.lock:
            if not c.review or body.review != c.review or c.proposal is None:
                raise HTTPException(409, 'Review changed. Read the latest review before confirming.')
            return await execute(c)

    return app


app = create_app()
