import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from card_servicing.models import CardCreate
from card_chat.config import settings
from card_chat.gateway import connect
from card_chat.provider import make_model
from card_chat.agent import CardServicingAgent, CustomerReply, ProposedToolCall


@dataclass
class Conversation:
    history: list = field(default_factory=list)
    draft: dict = field(default_factory=dict)
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
        return {'message': text, 'review': c.review}

    def review(c):
        card = CardCreate.model_validate(c.draft)
        c.draft = card.model_dump()
        c.review = uuid4().hex
        # Even short accounts must not reveal the entire value.
        account = c.draft['account_number']
        masked = '*' * max(4, len(account) - 4) + (account[-4:] if len(account) > 4 else '')
        return reply(c, f"Please review: given name {card.given_name}; user name {card.user_name}; "
                     f"account {masked}. Click Create card to confirm, or tell me what to change.")

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
                return reply(c, 'This submission is fixed. Retry with Create card, or start a new conversation for another request.')
            c.history.append({'role': 'user', 'content': body.text})
            try:
                turn = await app.state.agent.interpret_turn(c.history, c.draft, allowed_tools={})
                if not isinstance(turn, CustomerReply):
                    raise ValueError('Tool calls are not allowed before confirmation')
            except Exception:
                c.review = None
                return reply(c, 'The language service is unavailable or returned an invalid response. Please try again.')
            if not turn.supplied_fields and not turn.requests_review:
                c.review = None
                return reply(c, turn.text)
            candidate = {**c.draft, **turn.supplied_fields}
            # Validate partial updates with the exact existing request model.
            try:
                validated = CardCreate.model_validate({**{k: 'pending' for k in CardCreate.model_fields}, **candidate})
            except ValidationError:
                c.review = None
                return reply(c, 'Please provide nonblank text: names up to 100 characters and account number up to 64, without control characters.')
            c.draft = {k: v for k, v in validated.model_dump().items() if k in candidate}
            c.review = None
            if set(c.draft) != set(CardCreate.model_fields):
                return reply(c, turn.text)
            return review(c)

    @app.post('/conversations/{cid}/confirm')
    async def confirm(cid: str, body: Confirmation):
        c = get(cid)
        async with c.lock:
            if not c.review or body.review != c.review:
                raise HTTPException(409, 'Review changed. Read the latest review before confirming.')
            if c.submission is None:
                c.submission = {'key': uuid4().hex, 'body': CardCreate.model_validate(c.draft).model_dump(), 'result': None}
            submission = c.submission
            if submission['result']:
                return reply(c, f"Created simulated card {submission['result']['card_number']} (reference {submission['result']['id']}).")
            try:
                proposal = await app.state.agent.interpret_turn(
                    c.history, submission['body'], allowed_tools={'create_card': app.state.gateway.schema})
                if (not isinstance(proposal, ProposedToolCall) or proposal.name != 'create_card'
                        or CardCreate.model_validate(proposal.arguments).model_dump() != submission['body']):
                    raise ValueError('Tool call does not match confirmation')
                result = await app.state.gateway.create(submission['body'], submission['key'])
                if result.get('ok') is not True or not result.get('id') or not result.get('card_number'):
                    return reply(c, result.get('error', 'Creation failed. Retry this submission with Create card.'))
                submission['result'] = result
                return reply(c, f"Created simulated card {result['card_number']} (reference {result['id']}).")
            except Exception:
                return reply(c, 'Could not complete creation. Click Create card to retry the same submission safely.')

    return app


app = create_app()
