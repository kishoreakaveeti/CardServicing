"""Real stdio MCP + real HTTP API; model calls are replaced, never cloud calls."""
import asyncio
from contextlib import contextmanager
import socket
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from card_chat.gateway import connect
from card_chat.main import create_app
from card_chat.provider import Extraction

BODY = {'given_name': 'Alex', 'user_name': 'alex.synthetic', 'account_number': '0012345678'}


class StubModel:
    def __init__(self):
        self.outputs = []
        self.proposals = 0
        self.tamper = False

    async def extract(self, history, draft):
        return Extraction.model_validate(self.outputs.pop(0))

    async def propose(self, body, schema):
        assert 'idempotency_key' not in schema['properties']
        self.proposals += 1
        return 'create_card', {**body, **({'account_number': '999'} if self.tamper else {})}

    async def close(self):
        pass


@contextmanager
def running_api():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # Factory mode uses isolated in-memory storage, with real HTTP in a separate process.
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'card_servicing.main:create_app',
                                '--factory', '--port', str(port)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                if httpx.get(url + '/health', timeout=.2).status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError('REST process did not start')
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_mcp_discovery_and_real_rest_replay(monkeypatch):
    with running_api() as url:
        monkeypatch.setenv('CARD_API_BASE_URL', url)

        async def scenario():
            async with connect() as gateway:
                assert set(gateway.schema['properties']) == set(BODY)
                first = await gateway.create(BODY, 'synthetic-mcp-test')
                replay = await gateway.create(BODY, 'synthetic-mcp-test')
                assert first['ok'] is True
                assert replay == first
                assert (await gateway.create({**BODY, 'given_name': 'Sam'}, 'synthetic-mcp-test'))['ok'] is False
        asyncio.run(scenario())


def test_full_chat_real_mcp_retry_after_commit(monkeypatch):
    from card_chat.gateway import Gateway as RealGateway

    original = RealGateway.create
    results, calls = [], []

    async def lose_first_response(self, body, key):
        result = await original(self, body, key)
        calls.append((body.copy(), key))
        results.append(result)
        if len(results) == 1:
            raise TimeoutError('committed but response lost')
        return result

    monkeypatch.setattr(RealGateway, 'create', lose_first_response)
    with running_api() as url:
        monkeypatch.setenv('CARD_API_BASE_URL', url)
        model = StubModel()
        model.outputs = [{'intent': 'create', 'fields': BODY}]
        with TestClient(create_app(model=model)) as client:
            cid = client.post('/conversations').json()['id']
            path = f'/conversations/{cid}'
            review = client.post(path + '/messages', json={'text': 'synthetic details'}).json()['review']
            assert calls == []
            confirmation = {'review': review}
            assert 'retry' in client.post(path + '/confirm', json=confirmation).json()['message']
            assert 'Created simulated card' in client.post(path + '/confirm', json=confirmation).json()['message']
            client.post(path + '/confirm', json=confirmation)
            assert len(calls) == 2
            assert calls[0] == calls[1]
            assert results[0] == results[1] and results[0]['ok'] is True


class Gateway:
    schema = {'properties': {key: {'type': 'string'} for key in BODY}}

    def __init__(self):
        self.calls = []
        self.fail = False

    async def create(self, body, key):
        self.calls.append((body.copy(), key))
        if self.fail:
            self.fail = False
            raise TimeoutError('response lost after commit')
        return {'ok': True, 'id': 'synthetic-id', 'card_number': 'SIM-' + 'a' * 32}


def test_conversation_confirmation_and_retry():
    model, gateway = StubModel(), Gateway()
    with TestClient(create_app(model, gateway)) as client:
        assert 'Create card' in client.get('/').text
        data = client.post('/conversations').json()
        assert data['message'] == 'Hi! What would you like help with?'
        path = '/conversations/' + data['id']

        def say(intent, fields=None):
            model.outputs.append({'intent': intent, 'fields': fields or {}})
            return client.post(path + '/messages', json={'text': 'synthetic input'}).json()

        assert 'currently supports' in say('unsupported')['message']
        assert 'Would you like' in say('servicing')['message']
        assert 'given name' in say('create')['message']
        assert 'user name' in say('create', {'given_name': 'Alex'})['message']
        assert 'account number' in say('create', {'user_name': 'alex.synthetic'})['message']
        review = say('create', {'account_number': '0012345678'})
        assert '0012345678' not in review['message'] and '******5678' in review['message']
        assert gateway.calls == [] and model.proposals == 0
        fresh = say('create', {'given_name': 'Sam'})
        assert client.post(path + '/confirm', json={'review': review['review']}).status_code == 409
        model.tamper = True
        assert 'Could not' in client.post(path + '/confirm', json={'review': fresh['review']}).json()['message']
        assert gateway.calls == []
        model.tamper = False
        gateway.fail = True
        confirmation = {'review': fresh['review']}
        assert 'retry' in client.post(path + '/confirm', json=confirmation).json()['message']
        result = client.post(path + '/confirm', json=confirmation).json()
        assert 'Created simulated card SIM-' in result['message']
        assert gateway.calls[0] == gateway.calls[1]
        assert gateway.calls[0][0]['account_number'] == '0012345678'
        client.post(path + '/confirm', json=confirmation)
        assert len(gateway.calls) == 2


@pytest.mark.parametrize('fields', [{'account_number': 123}, {'given_name': 'x' * 101}, {'extra': 'x'}])
def test_invalid_extraction_never_writes(fields):
    model, gateway = StubModel(), Gateway()
    with TestClient(create_app(model, gateway)) as client:
        cid = client.post('/conversations').json()['id']
        model.outputs.append({'intent': 'create', 'fields': {**BODY, **fields}})
        response = client.post(f'/conversations/{cid}/messages', json={'text': 'synthetic'})
        assert response.json()['review'] is None
        assert gateway.calls == []


def test_anthropic_adapter_keeps_tools_out_of_extraction():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from card_chat.provider import AnthropicModel

    async def scenario():
        adapter = AnthropicModel({'ANTHROPIC_API_KEY': 'synthetic-test-key', 'MODEL_ID': 'synthetic-model'})
        try:
            create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(
                type='text', text='{"intent":"create","fields":{"account_number":"0012345678"}}')]))
            adapter.client.messages.create = create
            extracted = await adapter.extract([{'role': 'user', 'content': 'synthetic'}], {})
            assert extracted.fields['account_number'] == '0012345678'
            assert 'tools' not in create.call_args.kwargs
            create.return_value = SimpleNamespace(content=[SimpleNamespace(type='tool_use', name='create_card', input=BODY)])
            assert await adapter.propose(BODY, Gateway.schema) == ('create_card', BODY)
            request = create.call_args.kwargs
            assert request['tools'][0]['input_schema'] == Gateway.schema
            assert 'idempotency_key' not in str(request)
            create.return_value = SimpleNamespace(content=[])
            with pytest.raises(ValueError):
                await adapter.propose(BODY, Gateway.schema)
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_agent_turn_boundary_and_workflow_owned_snapshots():
    from card_chat.agent import CardServicingAgent, CustomerReply, ProposedToolCall

    class MutatingModel(StubModel):
        async def extract(self, history, draft):
            history.clear()
            draft['given_name'] = 'Changed by adapter'
            return await super().extract(history, draft)

        async def propose(self, body, schema):
            body['given_name'] = 'Changed by adapter'
            schema.clear()
            return 'create_card', body

    async def scenario():
        model = MutatingModel()
        agent = CardServicingAgent(model)
        history = [{'role': 'user', 'content': 'synthetic details'}]
        draft = BODY.copy()
        tools = {'create_card': {'properties': {'given_name': {'type': 'string'}}}}
        model.outputs = [{'intent': 'create', 'fields': {'account_number': '0012345678'}}]
        turn = await agent.interpret_turn(history, draft, allowed_tools={})
        assert isinstance(turn, CustomerReply)
        assert turn.supplied_fields == {'account_number': '0012345678'}
        assert turn.requests_review
        assert model.proposals == 0
        proposal = await agent.interpret_turn(history, draft, allowed_tools=tools)
        assert isinstance(proposal, ProposedToolCall)
        assert proposal.name == 'create_card'
        assert draft == BODY and len(history) == 1
        assert tools['create_card']['properties']['given_name'] == {'type': 'string'}
        with pytest.raises(ValueError):
            await agent.interpret_turn(history, draft, allowed_tools={'freeze_card': {}})

    asyncio.run(scenario())
