"""Capture actual Anthropic HTTP JSON and verify the private execution boundary."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient
import httpx
import pytest

from card_chat.catalogue import load_catalogue
from card_chat.model_context import build_context, tools_for_model, PRIVATE_NOTICE
from card_chat.provider import AnthropicModel
from card_chat.security import Security
from card_chat.turns import CustomerReply, ProposedToolCall
from test_chat import BODY, Gateway, StubModel, create_app, prepare_review, running_api
from test_http_services import identity


def mocked_claude(payloads, *, cache=False, choose=None):
    def transport(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        context = json.loads(payload['messages'][0]['content'])
        draft = context['collected_details']
        name = choose or draft.get('tool')
        content = ([{'type': 'tool_use', 'id': 'call_synthetic', 'name': name, 'input': draft.get('arguments', {})}]
                   if name else [{'type': 'text', 'text': 'Which action would you like to review?'}])
        return httpx.Response(200, json={'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant',
            'model': 'synthetic-model', 'content': content, 'stop_reason': 'tool_use' if name else 'end_turn',
            'stop_sequence': None, 'usage': {'input_tokens': 10, 'output_tokens': 4,
                'cache_creation_input_tokens': 5 if cache else 0, 'cache_read_input_tokens': 7 if cache else 0}})
    adapter = AnthropicModel({'ANTHROPIC_API_KEY': 'synthetic-key', 'MODEL_ID': 'synthetic-model',
                             'ANTHROPIC_PROMPT_CACHE': str(cache).lower()})
    original = adapter.client
    adapter.client = AsyncAnthropic(api_key='synthetic-key', http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(transport)), max_retries=0)
    # No network session was opened on the original client.
    asyncio.run(original.close())
    return adapter


def completed(client, path, body, asynchronous, *, headers=None, key=None):
    response = client.post(path, json=body, headers={**(headers or {}), 'Idempotency-Key': key or uuid4().hex,
        **({'Prefer': 'respond-async'} if asynchronous else {})})
    assert response.status_code in (200, 202), response.text
    if response.status_code == 200:
        return response.json()
    status_url = response.json()['status_url']
    for _ in range(200):
        job = client.get(status_url, headers=headers or {}).json()
        if job['status'] == 'succeeded':
            return job['result']
        assert job['status'] != 'failed', job
        time.sleep(.01)
    raise AssertionError('Operation did not finish')


@pytest.mark.parametrize('cache', [False, True])
def test_actual_anthropic_payload_allowlists_all_sources(cache, caplog):
    payloads = []
    adapter = mocked_claude(payloads, cache=cache)
    secrets = [BODY['account_number'], '4111111111111111', 'CVV-987', 'PIN-4321', 'sk-ant-secret-test',
               'PRIVATE_GIVEN_SENTINEL', 'private.user@example.invalid', 'UNNEEDED_CUSTOMER_IDENTIFIER']
    history = [{'role': 'user', 'content': f'account_number: {secrets[0]}, card_number: {secrets[1]}, cvv: {secrets[2]}'},
               {'role': 'user', 'content': f'given_name: {secrets[5]}, user_name: {secrets[6]}'},
               {'role': 'assistant', 'content': secrets[-1]},
               {'role': 'tool', 'content': json.dumps({'result': secrets})},
               {'role': 'user', 'content': 'Please explain the available actions.'}]
    draft = {'tool': 'create_card', 'arguments': {**BODY, 'pin': secrets[3], 'api_key': secrets[4]},
             'result': {'id': secrets[-1]}, 'customer_id': secrets[-1]}
    tools = Gateway().tools
    tools['create_card']['input_schema']['properties']['given_name']['default'] = secrets[5]
    tools['create_card']['input_schema']['examples'] = [{'password': secrets[4]}]
    async def scenario():
        try:
            await adapter.turn(history, draft, tools)
        finally:
            await adapter.close()
    asyncio.run(scenario())
    outgoing = json.dumps(payloads[0])
    assert all(secret not in outgoing for secret in secrets)
    assert payloads[0]['tool_choice']['type'] == 'auto'
    assert 'tool_result' not in outgoing and 'customer_id' not in outgoing
    if cache:
        assert payloads[0]['system'][-1]['cache_control'] == {'type': 'ephemeral'}
    else:
        assert 'cache_control' not in outgoing
    assert 'cache_control' not in json.dumps(payloads[0]['messages'])
    assert adapter.last_usage['input_tokens'] == 10
    assert all(secret not in caplog.text for secret in secrets)


@pytest.mark.parametrize('asynchronous', [False, True])
def test_mocked_claude_real_remote_mcp_correct_private_write_and_zero_call_retries(monkeypatch, tmp_path, asynchronous):
    payloads, executions = [], []
    model = mocked_claude(payloads, cache=True)
    from card_chat.gateway import Gateway as MCPGateway
    original = MCPGateway.execute
    async def lose_after_commit(self, name, arguments, key=None):
        result = await original(self, name, arguments, key)
        executions.append((name, arguments.copy(), key, result))
        if len(executions) == 1:
            raise TimeoutError('Synthetic response loss after commit')
        return result
    monkeypatch.setattr(MCPGateway, 'execute', lose_after_commit)
    database = tmp_path / 'cards.db'
    with running_api(runtime_env={'TELEMETRY_SINK': 'none', 'SQLITE_PATH': str(database)}) as url:
        monkeypatch.setenv('CARD_API_BASE_URL', url)
        with TestClient(create_app(model=model)) as client:
            cid = client.post('/conversations').json()['id']
            path = f'/conversations/{cid}'
            options = client.get(path + '/inputs').json()
            assert BODY['account_number'] not in json.dumps(options)
            ref = next(field['options'][0]['ref'] for field in options['tools'][0]['fields'] if field.get('options'))
            review = completed(client, path + '/inputs', {'tool': 'create_card', 'fields': {'given_name': 'Alex'},
                               'selections': {'account_number': ref}}, asynchronous)
            assert payloads == [] and executions == []
            review = completed(client, path + '/messages', {'text': 'Please create the card using these details.'}, asynchronous)
            assert review['review'] and len(payloads) == 1 and executions == []
            assert all(value not in json.dumps(payloads) for value in BODY.values())
            body = {'review': review['review']}
            uncertain = completed(client, path + '/confirm', body, asynchronous)
            assert uncertain['can_confirm'] and 'retry' in uncertain['message']
            result = completed(client, path + '/confirm', body, asynchronous)
            replay = completed(client, path + '/confirm', body, asynchronous)
            assert replay['message'] == result['message'] and 'SIM-' in result['message']
            assert len(payloads) == 1 and len(executions) == 2
            assert executions[0][:3] == executions[1][:3]
            assert executions[1][1] == BODY and executions[1][3]['result']['account_number'] == BODY['account_number']
            assert result['usage'] == {'model_calls': 1, 'input_tokens': 10, 'output_tokens': 4,
                'cache_creation_input_tokens': 5, 'cache_read_input_tokens': 7, 'completed_actions': 1}
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0] == 1


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('paste', ['0012345678', '4111 1111 1111 1111', 'CVV 987', 'PIN 4321',
                                  'password: synthetic-secret', 'sk-ant-private-test', json.dumps(BODY),
                                  'customer_id: CUST_ALPHA'])
def test_private_pastes_make_zero_calls_and_are_not_journalled(paste, asynchronous):
    model = StubModel()
    with TestClient(create_app(model, Gateway())) as client:
        cid = client.post('/conversations').json()['id']
        reply = completed(client, f'/conversations/{cid}/messages', {'text': paste}, asynchronous)
        assert reply['message'] == PRIVATE_NOTICE and model.seen_tools == []
        row = client.app.state.journal.load(cid, 'isolated-test')
        bodies = [row['body'] for row in client.app.state.journal.db.execute('SELECT body FROM operations')]
        assert paste not in json.dumps(row) and paste not in json.dumps(bodies)


def test_permissions_reference_ownership_stale_review_and_revocation(identity):
    config, headers = identity
    model, gateway = StubModel(), Gateway()
    with TestClient(create_app(model, gateway, config=config)) as client:
        good, sam = headers(), headers('sam')
        cid = client.post('/conversations', headers=good).json()['id']
        other = client.post('/conversations', headers=sam).json()['id']
        options = client.get(f'/conversations/{cid}/inputs', headers=good).json()
        ref = next(f['options'][0]['ref'] for f in options['tools'][0]['fields'] if f.get('options'))
        forbidden = client.post(f'/conversations/{other}/inputs', headers=sam, json={'tool': 'create_card',
            'fields': {'given_name': 'Sam'}, 'selections': {'account_number': ref}})
        assert forbidden.status_code == 422 and ref not in forbidden.text
        assert client.get(f'/conversations/{cid}/inputs', headers=sam).status_code == 404
        review = prepare_review(client, cid, headers=good).json()
        updated = prepare_review(client, cid, {**BODY, 'given_name': 'Sam'}, headers=good).json()
        assert client.post(f'/conversations/{cid}/confirm', headers=good, json={'review': review['review']}).status_code == 409
        restricted = {**good, 'X-Customer-Delegation': Security(config).issue_customer({
            'sub': 'alex', 'preferred_username': 'alex.synthetic', 'accounts': ['0012345678'], 'permissions': []})}
        assert client.get(f'/conversations/{cid}/inputs', headers=restricted).json()['tools'] == []
        response = client.post(f'/conversations/{cid}/messages', headers=restricted, json={'text': 'What can I do?'})
        assert 'No eligible' in response.json()['message'] and model.seen_tools == []
        # A fresh review cannot be executed after permission or account revocation.
        updated = prepare_review(client, cid, headers=good).json()
        assert client.post(f'/conversations/{cid}/confirm', headers=restricted, json={'review': updated['review']}).status_code == 403
        no_account = {**good, 'X-Customer-Delegation': Security(config).issue_customer({
            'sub': 'alex', 'preferred_username': 'alex.synthetic', 'accounts': []})}
        assert client.post(f'/conversations/{cid}/confirm', headers=no_account, json={'review': updated['review']}).status_code == 403
        assert gateway.calls == []


def test_changes_of_action_corrections_and_workflow_eligibility():
    catalogue = load_catalogue({'TOOL_CATALOGUE_PATH': str(Path(__file__).parent / 'fixtures/tool-catalogue.json')})
    gateway, model = Gateway(), StubModel()
    gateway.catalogue = catalogue
    gateway.tools = {name: spec.definition() for name, spec in catalogue.items()}
    with TestClient(create_app(model, gateway)) as client:
        cid = client.post('/conversations').json()['id']
        path = f'/conversations/{cid}'
        prepare_review(client, cid)
        model.outputs = [CustomerReply('Which card should I block?')]
        client.post(path + '/messages', json={'text': 'Block a card instead.'})
        assert set(model.seen_tools[-1]) == {'create_card', 'block_card'}
        prepare_review(client, cid, {'card_id': '000042', 'reason': 'original reason'}, 'block_card')
        model.outputs = [ProposedToolCall('block_card', {'reason': 'updated reason'})]
        corrected = client.post(path + '/messages', json={'text': 'Use updated reason.'}).json()
        assert corrected['review'] and 'updated reason' in corrected['message']
        before = len(model.seen_tools)
        clarification = client.post(path + '/messages', json={'text': 'Use the first one.'}).json()
        assert 'cannot safely infer' in clarification['message'] and len(model.seen_tools) == before
        assert gateway.calls == []
    model = StubModel(); model.outputs = [CustomerReply('Choose the supported action.')]
    with TestClient(create_app(model, gateway, config={'MODEL_ALLOWED_TOOLS': 'block_card'})) as client:
        cid = client.post('/conversations').json()['id']
        client.post(f'/conversations/{cid}/messages', json={'text': 'Which actions can I use?'})
        assert set(model.seen_tools[0]) == {'block_card'}


def test_context_recent_turn_bound_and_cache_prefix_stability():
    catalogue = load_catalogue({'TOOL_CATALOGUE_PATH': str(Path(__file__).parent / 'fixtures/tool-catalogue.json')})
    tools = tools_for_model(catalogue)
    payloads = []
    adapter = mocked_claude(payloads, cache=True)
    history = [{'role': 'user', 'content': 'Earlier request ' + chr(65 + i)} for i in range(20)]
    async def scenario():
        try:
            await adapter.turn(history, {}, tools)
            await adapter.turn([{'role': 'user', 'content': 'A different intent'}], {}, dict(reversed(list(tools.items()))))
        finally:
            await adapter.close()
    asyncio.run(scenario())
    assert len(json.loads(payloads[0]['messages'][0]['content'])['conversation']) == 8
    assert payloads[0]['system'] == payloads[1]['system'] and payloads[0]['tools'] == payloads[1]['tools']
    assert payloads[0]['messages'] != payloads[1]['messages']


def test_invalid_structured_correction_invalidates_review_without_echoing_values():
    model, gateway = StubModel(), Gateway()
    with TestClient(create_app(model, gateway)) as client:
        cid = client.post('/conversations').json()['id']
        review = prepare_review(client, cid).json()['review']
        bad = 'PRIVATE_INVALID_' + 'x' * 110
        response = client.post(f'/conversations/{cid}/inputs', json={'tool': 'create_card', 'fields': {'given_name': bad}})
        assert response.status_code == 422 and bad not in response.text
        assert client.post(f'/conversations/{cid}/confirm', json={'review': review}).status_code == 409
        assert gateway.calls == [] and model.seen_tools == []


def test_model_cannot_invent_private_values_or_reuse_other_conversation_facts():
    model, gateway = StubModel(), Gateway()
    with TestClient(create_app(model, gateway)) as client:
        first = client.post('/conversations').json()['id']
        second = client.post('/conversations').json()['id']
        prepare_review(client, first)
        stolen = client.app.state.journal.load(first, 'isolated-test')['draft']['arguments']
        model.outputs = [ProposedToolCall('create_card', stolen), ProposedToolCall('create_card', BODY)]
        for _ in range(2):
            reply = client.post(f'/conversations/{second}/messages', json={'text': 'Create a card.'}).json()
            assert reply['review'] is None and 'secure action form' in reply['message']
        assert gateway.calls == []


def test_permissions_filter_individual_tools_before_the_model():
    model, gateway = StubModel(), Gateway()
    catalogue = load_catalogue({'TOOL_CATALOGUE_PATH': str(Path(__file__).parent / 'fixtures/tool-catalogue.json')})
    catalogue['block_card'].entry.permissions = ['cards:block']
    gateway.catalogue = catalogue
    model.outputs = [CustomerReply('Use an eligible action.')]
    with TestClient(create_app(model, gateway)) as client:
        cid = client.post('/conversations').json()['id']
        client.post(f'/conversations/{cid}/messages', json={'text': 'What actions are available?'})
        assert set(model.seen_tools[0]) == {'create_card'}


def test_malformed_verified_account_facts_fail_closed(identity):
    from fastapi import HTTPException
    config, _ = identity
    security = Security(config)
    token = security.issue_customer({'sub': 'alex', 'preferred_username': 'alex.synthetic', 'accounts': '0012345678'})
    with pytest.raises(HTTPException) as caught:
        security.customer(token, 'agent')
    assert caught.value.status_code == 401


@pytest.mark.parametrize('text', ['Use the first one.', 'Use the one from before.',
                                  'Select my previous account.', 'Use the card from last time.'])
def test_ambiguous_older_selections_clarify_without_model_work(text):
    model = StubModel()
    with TestClient(create_app(model, Gateway())) as client:
        cid = client.post('/conversations').json()['id']
        reply = client.post(f'/conversations/{cid}/messages', json={'text': text}).json()
        assert 'cannot safely infer' in reply['message'] and model.seen_tools == []


@pytest.mark.parametrize('field', ['customer_id', 'cvv', 'email'])
def test_catalogue_cannot_opt_private_identifier_fields_into_model_context(field):
    from card_chat.catalogue import compile_entry, Entry, CatalogueError
    fixture = Path(__file__).parent / 'fixtures'
    document = json.loads((fixture / 'tools.openapi.json').read_text())
    entry = json.loads((fixture / 'tool-catalogue.json').read_text())['tools'][1]
    document['components']['schemas']['BlockRequest']['properties'][field] = {'type': 'string'}
    entry['context_fields'] = [field]
    with pytest.raises(CatalogueError, match='cannot be model-visible'):
        compile_entry(Entry(**entry), document)


def test_numeric_card_value_cannot_hide_in_a_public_scalar_fact():
    payloads = []
    adapter = mocked_claude(payloads)
    tools = {'budget': {'name': 'budget', 'description': 'Explain a budget.', 'input_schema': {
        'type': 'object', 'properties': {'amount': {'type': 'number', 'x-model-input': 'safe'}}}}}
    async def scenario():
        try:
            await adapter.turn([{'role': 'user', 'content': 'Explain this budget.'}],
                               {'tool': 'budget', 'arguments': {'amount': 4111111111111111}}, tools)
        finally:
            await adapter.close()
    asyncio.run(scenario())
    assert '4111111111111111' not in json.dumps(payloads)


def test_maximum_length_benign_message_is_not_misclassified_as_a_private_paste():
    model = StubModel(); model.outputs = [CustomerReply('Please choose an action.')]
    text = 'safe ' * 800
    with TestClient(create_app(model, Gateway())) as client:
        cid = client.post('/conversations').json()['id']
        response = client.post(f'/conversations/{cid}/messages', json={'text': text}).json()
        assert response['message'] == 'Please choose an action.'
        assert model.seen_context[-1][0][-1]['content'] == text
