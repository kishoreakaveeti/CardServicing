"""Real MCP/HTTP tests with provider-neutral stub model outcomes."""
import asyncio
from contextlib import contextmanager
import socket
import subprocess
import sys
import time
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from card_chat.catalogue import load_catalogue
from card_chat.gateway import connect
from card_chat.main import create_app
from card_chat.turns import CustomerReply, ProposedToolCall

BODY = {'given_name': 'Alex', 'user_name': 'alex.synthetic', 'account_number': '0012345678'}


class StubModel:
    def __init__(self):
        self.outputs = []
        self.seen_tools = []
        self.seen_context = []

    async def turn(self, history, collected, tools):
        self.seen_tools.append(tools)
        self.seen_context.append((history, collected))
        return self.outputs.pop(0)

    async def close(self):
        pass


@contextmanager
def running_api(*, runtime_env=None, stdout=None, app_target=None):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # Optional runtime mode exercises the actual entry point with isolated SQLite.
    target = ['card_servicing.main:app'] if runtime_env is not None else ['card_servicing.main:create_app', '--factory']
    if app_target:
        target = [app_target]
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', *target, '--port', str(port)],
                               env={**os.environ, **(runtime_env or {})},
                               stdout=stdout if stdout is not None else subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
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
                assert set(gateway.tools['create_card']['input_schema']['properties']) == set(BODY)
                first = await gateway.execute('create_card', BODY, 'synthetic-mcp-test')
                replay = await gateway.execute('create_card', BODY, 'synthetic-mcp-test')
                assert first['ok'] is True
                assert replay == first
                assert first['result']['account_number'] == '0012345678'
                assert (await gateway.execute('create_card', {**BODY, 'given_name': 'Sam'}, 'synthetic-mcp-test'))['ok'] is False
        asyncio.run(scenario())


@pytest.mark.parametrize('runtime_sink', [None, 'stdout', 'none'])
def test_full_chat_real_mcp_retry_after_commit(monkeypatch, tmp_path, runtime_sink):
    from card_chat.gateway import Gateway as RealGateway
    original = RealGateway.execute
    results, calls = [], []
    async def lose_first_response(self, name, body, key=None):
        result = await original(self, name, body, key)
        calls.append((name, body.copy(), key))
        results.append(result)
        if len(results) == 1:
            raise TimeoutError('committed but response lost')
        return result
    monkeypatch.setattr(RealGateway, 'execute', lose_first_response)
    runtime = None if runtime_sink is None else {
        'TELEMETRY_SINK': runtime_sink, 'CARD_STORE': 'sqlite',
        'SQLITE_PATH': str(tmp_path / 'cards.db'),
        'MONGO_USER': '', 'MONGO_PASSWORD': '', 'MONGODB_PORT': 'invalid',
    }
    with running_api(runtime_env=runtime) as url:
        monkeypatch.setenv('CARD_API_BASE_URL', url)
        model = StubModel()
        model.outputs = [ProposedToolCall('create_card', BODY)]
        with TestClient(create_app(model=model)) as client:
            cid = client.post('/conversations').json()['id']
            path = f'/conversations/{cid}'
            review = client.post(path + '/messages', json={'text': 'synthetic details'}).json()['review']
            assert calls == []
            confirmation = {'review': review}
            assert 'retry' in client.post(path + '/confirm', json=confirmation).json()['message']
            assert 'SIM-' in client.post(path + '/confirm', json=confirmation).json()['message']
            client.post(path + '/confirm', json=confirmation)
            assert len(calls) == 2
            assert calls[0] == calls[1]
            assert results[0] == results[1] and results[0]['ok'] is True
            assert len(model.seen_tools) == 1  # No model invocation at confirmation/retry.
    if runtime is not None:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(runtime['SQLITE_PATH'])) as database:
            assert database.execute('SELECT count(*) FROM cards').fetchone()[0] == 1
            assert database.execute('SELECT count(*) FROM card_idempotency').fetchone()[0] == 1


class Gateway:
    def __init__(self):
        self.catalogue = load_catalogue()
        self.tools = {name: spec.definition() for name, spec in self.catalogue.items()}
        self.calls = []
        self.fail = False

    async def execute(self, name, body, key=None):
        self.calls.append((name, body.copy(), key))
        if self.fail:
            self.fail = False
            raise TimeoutError('response lost after commit')
        return {'ok': True, 'result': {'id': 'synthetic-id', 'card_number': 'SIM-' + 'a' * 32}}


def test_conversation_confirmation_and_retry():
    model, gateway = StubModel(), Gateway()
    with TestClient(create_app(model, gateway)) as client:
        assert 'Confirm action' in client.get('/').text
        data = client.post('/conversations').json()
        assert data['message'] == 'Hi! What would you like help with?'
        path = '/conversations/' + data['id']
        def say(turn, text='synthetic input'):
            model.outputs.append(turn)
            return client.post(path + '/messages', json={'text': text}).json()
        assert 'creating' in say(CustomerReply('This demo supports creating a card, not freezing it.'))['message']
        assert 'given name' in say(CustomerReply('What is your given name?'))['message']
        partial = say(ProposedToolCall('create_card', {'given_name': 'Alex', 'user_name': 'alex.synthetic'}))
        assert partial['review'] is None and 'Account number' in partial['message']
        review = say(ProposedToolCall('create_card', BODY))
        assert '0012345678' not in review['message'] and '******5678' in review['message']
        assert review['confirmation_label'] == 'Create card'
        assert gateway.calls == []
        fresh = say(ProposedToolCall('create_card', {**BODY, 'given_name': 'Sam'}))
        assert client.post(path + '/confirm', json={'review': review['review']}).status_code == 409
        count = len(model.seen_tools)
        gateway.fail = True
        confirmation = {'review': fresh['review']}
        assert 'retry' in client.post(path + '/confirm', json=confirmation).json()['message']
        result = client.post(path + '/confirm', json=confirmation).json()
        assert 'SIM-' in result['message']
        assert gateway.calls[0] == gateway.calls[1]
        assert gateway.calls[0][1]['account_number'] == '0012345678'
        client.post(path + '/confirm', json=confirmation)
        assert len(gateway.calls) == 2 and len(model.seen_tools) == count
        assert model.seen_context[-1][1]['arguments'] == BODY


@pytest.mark.parametrize('fields', [{'account_number': 123}, {'given_name': 'x' * 101}, {'extra': 'x'}, {'account_number': 'x\x00'}])
def test_invalid_arguments_never_write(fields):
    model, gateway = StubModel(), Gateway()
    with TestClient(create_app(model, gateway)) as client:
        cid = client.post('/conversations').json()['id']
        model.outputs.append(ProposedToolCall('create_card', {**BODY, **fields}))
        response = client.post(f'/conversations/{cid}/messages', json={'text': 'synthetic'})
        assert response.json()['review'] is None
        assert gateway.calls == []


def test_unknown_tool_and_plain_reply_invalidate_review():
    model, gateway = StubModel(), Gateway()
    with TestClient(create_app(model, gateway)) as client:
        path = '/conversations/' + client.post('/conversations').json()['id']
        model.outputs = [ProposedToolCall('create_card', BODY), ProposedToolCall('unknown_tool', BODY), CustomerReply('Please clarify your request.')]
        review = client.post(path + '/messages', json={'text': 'new card'}).json()['review']
        assert client.post(path + '/messages', json={'text': 'different action'}).json()['review'] is None
        assert client.post(path + '/confirm', json={'review': review}).status_code == 409
        assert client.post(path + '/messages', json={'text': 'unclear'}).json()['review'] is None
        assert gateway.calls == []


def test_anthropic_adapter_uses_automatic_tool_selection():
    from card_chat.provider import AnthropicModel
    async def scenario():
        adapter = AnthropicModel({'ANTHROPIC_API_KEY': 'synthetic-test-key', 'MODEL_ID': 'synthetic-model'})
        try:
            create = AsyncMock(return_value=SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text='What is your given name?')]))
            adapter.client.messages.create = create
            tools = Gateway().tools
            reply = await adapter.turn([{'role': 'user', 'content': 'new card'}], {}, tools)
            assert isinstance(reply, CustomerReply)
            assert create.call_args.kwargs['tools'] == list(tools.values())
            assert create.call_args.kwargs['tool_choice']['type'] == 'auto'
            assert 'idempotency_key' not in str(create.call_args.kwargs)
            create.return_value = SimpleNamespace(stop_reason='tool_use', content=[SimpleNamespace(type='tool_use', name='create_card', input=BODY)])
            assert await adapter.turn([], {}, tools) == ProposedToolCall('create_card', BODY)
            create.return_value.content *= 2
            assert 'choose one' in (await adapter.turn([], {}, tools)).text
        finally:
            await adapter.close()
    asyncio.run(scenario())


def test_agent_passes_copied_context_and_tools():
    from card_chat.agent import CardServicingAgent
    class MutatingModel(StubModel):
        async def turn(self, history, collected, tools):
            history.clear(); collected.clear(); tools.clear()
            return CustomerReply('Please clarify.')
    async def scenario():
        agent = CardServicingAgent(MutatingModel())
        history, collected, tools = [{'role':'user','content':'synthetic'}], {'tool':'create_card','arguments':BODY.copy()}, Gateway().tools
        assert isinstance(await agent.interpret_turn(history,collected,tools),CustomerReply)
        assert history and collected and tools
    asyncio.run(scenario())
