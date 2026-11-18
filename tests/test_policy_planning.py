"""Travel planning uses the existing retriever and independent banking guard."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from card_chat.policy.adapters.evaluator import GroundedPolicyEvaluator
from card_chat.policy.planning import (retrieve_for_plan, propose_plan, complete_assistance,
                                       needs_policy_planning)


REQUEST = "I'm in Singapore. I've lost my debit card. I'm returning to Sydney tomorrow and urgently need access to my money. I don't want my bank account frozen."


class Model:
    policy_context_tokens = 1250

    def __init__(self):
        self.calls = []

    async def policy_tool(self, name, schema, payload, instructions):
        self.calls.append((name, payload))
        if name == 'search_policy':
            return {'query': 'overseas lost card returning home digital availability emergency cash account freeze'}
        if name == 'explain_assistance_results':
            return {'message': 'The assistance checks are synthetic and unavailable. Please contact specialist support.'}
        policy = next(p for p in payload['passages'] if p['policy_id'] == 'POL-005')
        priority = ['check_digital_card_eligibility', 'check_emergency_cash_eligibility']
        if 'cash before digital' in policy['text']:
            priority.reverse()
        return {'actions': [{'action': name, 'rationale': 'Follow the retrieved assistance policy.',
            'policy_id': 'POL-005', 'version': policy['version'],
            'quote': 'Do not freeze the customer', 'requires_confirmation': name in ('block_card', 'request_specialist_handoff')}
            for name in ['block_card', *priority, 'request_specialist_handoff']]}


@pytest.fixture
def supervisor(policy_retriever, monkeypatch):
    # The privacy gate itself has dedicated regression tests. This deterministic
    # test double records payloads without needing the downloaded NLP model.
    monkeypatch.setattr('card_chat.policy.planning.safe_text', lambda text: text)
    monkeypatch.setattr('card_chat.policy.planning.redact_policy_value', lambda text: text)
    return SimpleNamespace(model=Model(), policy_retriever=policy_retriever,
                           policy_evaluator=GroundedPolicyEvaluator(policy_retriever))


def planned(supervisor):
    state = {'messages': [{'role': 'user', 'content': REQUEST}], 'allowed_tools': {'block_card': {}}}
    state.update(asyncio.run(retrieve_for_plan(supervisor, state)))
    assert state['next_step'] == 'policy_plan'
    state.update(asyncio.run(propose_plan(supervisor, state)))
    return state


def test_query_passages_plan_and_completed_results(supervisor):
    state = planned(supervisor)
    assert state['next_step'] == 'supervisor'
    context = state['policy_planning']
    assert context['searches'] == 1
    assert len(context['passages']) <= 3
    assert supervisor.model.calls[0][1]['request'] == REQUEST
    assert 'digital card availability' in supervisor.model.calls[1][1]['passages'][0]['text']
    assert all(row['outcome'] == 'validated' for row in context['validation'])
    answer = asyncio.run(complete_assistance(supervisor, state, 'Card blocked.'))
    assert 'synthetic' in answer
    assert supervisor.model.calls[-1][0] == 'explain_assistance_results'
    assert supervisor.model.calls[-1][1]['results'][0]['status'] == 'completed'
    assert 'passages' not in supervisor.model.calls[-1][1]


@pytest.mark.parametrize('mutation', ['freeze_account', 'issue_digital_card', 'no_confirmation', 'bad_version', 'bad_quote', 'no_auth'])
def test_deterministic_guard_rejects(supervisor, mutation):
    state = planned(supervisor)
    context = state['policy_planning']
    plan = deepcopy(context['plan'])
    tools = state['allowed_tools']
    first = plan['actions'][0]
    if mutation == 'no_confirmation':
        first['requires_confirmation'] = False
    elif mutation == 'bad_version':
        first['version'] = '99.0'
    elif mutation == 'bad_quote':
        first['quote'] = 'Fabricated policy content.'
    elif mutation == 'no_auth':
        tools = {}
    else:
        first['action'] = mutation
    result = supervisor.policy_evaluator.validate_assistance_plan(plan, context['passages'], tools)
    assert any(row['outcome'] == 'rejected' for row in result)


def test_missing_policy_and_retrieval_gate(supervisor, monkeypatch):
    monkeypatch.setattr(supervisor.policy_retriever, 'retrieve_mandatory', lambda **kwargs: [])
    state = {'messages': [{'role': 'user', 'content': REQUEST}]}
    assert asyncio.run(retrieve_for_plan(supervisor, state))['error'] == 'PolicyPlanningUnavailable'
    assert asyncio.run(propose_plan(supervisor, state))['error'] == 'PolicyPlanRejected'
    assert len(supervisor.model.calls) == 1


def test_context_cache_and_simple_request(supervisor):
    assert needs_policy_planning(REQUEST)
    assert not needs_policy_planning('Block my card.')
    state = planned(supervisor)
    before = len(supervisor.model.calls)
    asyncio.run(retrieve_for_plan(supervisor, state))
    assert len(supervisor.model.calls) == before


def test_simple_block_skips_model_classification(supervisor):
    from card_chat.orchestration.supervisor import SupervisorAgent
    agent = SupervisorAgent(supervisor.model, policy_retriever=supervisor.policy_retriever,
                            policy_evaluator=supervisor.policy_evaluator)
    result = asyncio.run(agent._classify_and_plan('Block my card.', {'block_card': {}}, {}))
    assert result[1] == ['block_card']
    assert not supervisor.model.calls


def test_policy_prose_can_change_model_plan(supervisor, tmp_path):
    import hashlib
    from card_chat.policy.adapters.document_source import InMemoryDocumentSource
    from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline
    state = planned(supervisor)
    original = state['policy_planning']['plan']['actions'][1]['action']
    retriever = supervisor.policy_retriever
    documents = retriever._doc_source.list_documents()
    for index, doc in enumerate(documents):
        if doc.policy_id == 'POL-005':
            text = doc.raw_content.replace('1.0.0', '1.0.1').replace(
                'checking digital card availability', 'checking emergency cash before digital card availability')
            documents[index] = doc.model_copy(update={'version': '1.0.1', 'raw_content': text,
                'conditions': {**doc.conditions, 'rules_version': '1.0.1'},
                'content_hash': hashlib.sha256(text.encode()).hexdigest()})
    retriever._doc_source = InMemoryDocumentSource(documents)
    PolicyIngestionPipeline(retriever._doc_source, retriever._embedder, retriever._store,
        audit_log_path=tmp_path/'priority-change.json').ingest_all()
    updated = planned(supervisor)
    assert updated['policy_planning']['plan']['actions'][1]['action'] != original
    assert updated['policy_planning']['passages'][0]['version'] == '1.0.1'


def test_conflicting_rules_fail_closed(supervisor, monkeypatch):
    original = supervisor.policy_retriever.retrieve_mandatory

    def conflict(**kwargs):
        return [e.model_copy(update={'conditions': {}}) if e.policy_id == 'POL-005' else e
                for e in original(**kwargs)]

    monkeypatch.setattr(supervisor.policy_retriever, 'retrieve_mandatory', conflict)
    state = {'messages': [{'role': 'user', 'content': REQUEST}]}
    assert asyncio.run(retrieve_for_plan(supervisor, state))['error'] == 'PolicyPlanningUnavailable'


def test_synthetic_available_skips_fallbacks(supervisor):
    state = planned(supervisor)
    supervisor.model.policy_demo_availability = {'check_digital_card_eligibility': True}
    asyncio.run(complete_assistance(supervisor, state, 'Card blocked.'))
    results = supervisor.model.calls[-1][1]['results']
    assert results[1]['status'] == 'synthetic_available'
    assert all(row['status'] == 'skipped_alternative_available' for row in results[2:])


def test_stale_content_stops_assistance_without_losing_block_success(supervisor, monkeypatch):
    state = planned(supervisor)
    document = supervisor.policy_retriever.get_document('POL-005')
    monkeypatch.setattr(supervisor.policy_retriever, 'get_document',
        lambda *args: document.model_copy(update={'content_hash': 'changed'}))
    before = len(supervisor.model.calls)
    result = asyncio.run(complete_assistance(supervisor, state, 'Card blocked.'))
    assert result.startswith('Card blocked.') and 'changed' in result
    assert len(supervisor.model.calls) == before


def test_final_model_failure_preserves_known_success(supervisor):
    state = planned(supervisor)

    async def unavailable(*args):
        raise RuntimeError('synthetic provider outage')

    supervisor.model.policy_tool = unavailable
    answer = asyncio.run(complete_assistance(supervisor, state, 'Card blocked.'))
    assert answer.startswith('Card blocked.') and 'Synthetic' in answer


def test_sdk_graph_confirmed_execution_and_replay(policy_retriever):
    """Exercise real SDK serialization, graph nodes, form review and durable resume."""
    import json
    import httpx
    from anthropic import AsyncAnthropic
    from fastapi.testclient import TestClient
    from card_chat.provider import AnthropicModel
    from test_policy_recommendations import application, block_review, state as snapshot

    model = AnthropicModel({'ANTHROPIC_API_KEY': 'synthetic-not-live', 'MODEL_ID': 'synthetic-model'})
    outbound = []

    def transport(request):
        body = json.loads(request.content)
        outbound.append(body)
        name = body['tools'][0]['name']
        if name in ('search_policy', 'propose_policy_plan', 'explain_assistance_results'):
            payload = json.loads(body['messages'][0]['content'])
            # These mocked responses exercise the actual SDK request boundary.
            if name == 'search_policy':
                result = {'query': 'overseas lost card returning home digital availability emergency cash'}
            elif name == 'propose_policy_plan':
                policy = payload['passages'][0]
                result = {'actions': [{'action': action, 'rationale': 'Follow the supplied assistance policy.',
                    'policy_id': 'POL-005', 'version': policy['version'],
                    'quote': 'Secure the lost card by blocking that card only',
                    'requires_confirmation': action in ('block_card', 'request_specialist_handoff')}
                    for action in ('block_card', 'check_digital_card_eligibility',
                                   'check_emergency_cash_eligibility', 'request_specialist_handoff')]}
            else:
                result = {'message': 'Synthetic assistance checks are unavailable. Please contact specialist support.'}
        else:
            name, result = 'block_card', {}
        return httpx.Response(200, json={'id': 'msg_plan', 'type': 'message', 'role': 'assistant',
            'model': 'synthetic-model', 'stop_reason': 'tool_use', 'stop_sequence': None,
            'usage': {'input_tokens': 101, 'output_tokens': 23},
            'content': [{'type': 'tool_use', 'id': 'call_plan', 'name': name, 'input': result}]})

    asyncio.run(model.client.close())
    model.client = AsyncAnthropic(api_key='synthetic-not-live', max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    app, gateway = application(policy_retriever, model)
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        path = '/conversations/' + cid
        response = client.post(path+'/messages', json={'text': REQUEST})
        assert response.status_code == 200
        saved = snapshot(client, app, cid)
        assert saved['policy_planning']['plan']['actions'][0]['action'] == 'block_card'
        assert not gateway.calls
        review = block_review(client, cid, 'lost overseas')
        response = client.post(path+'/confirm', json={'review': review['review']},
                               headers={'Idempotency-Key': 'travel-block'})
        assert response.status_code == 200
        assert len(gateway.calls) == 1 and gateway.calls[0][0] == 'block_card'
        assert 'Synthetic' in response.json()['message']
        count = len(outbound)
        assert client.post(path+'/confirm', json={'review': review['review']},
                           headers={'Idempotency-Key': 'travel-block'}).json() == response.json()
        assert len(outbound) == count
        assert [p['tools'][0]['name'] for p in outbound].count('search_policy') == 1
        text = json.dumps(outbound)
        assert 'private-card-reference' not in text and '0012345678' not in text


def test_optional_live_model_policy_planning(policy_retriever):
    import os
    if os.getenv('RUN_LIVE_POLICY_PLANNING') != '1':
        pytest.skip('Explicit opt-in required for paid live Anthropic calls')
    from fastapi.testclient import TestClient
    from card_chat.provider import AnthropicModel
    from test_policy_recommendations import application, state as snapshot
    model = AnthropicModel({'ANTHROPIC_API_KEY': os.environ['ANTHROPIC_API_KEY'],
                            'MODEL_ID': os.environ['MODEL_ID']})
    app, gateway = application(policy_retriever, model)
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        response = client.post('/conversations/'+cid+'/messages', json={'text': REQUEST})
        assert response.status_code == 200
        planning = snapshot(client, app, cid)['policy_planning']
        assert planning['plan']['actions'][0]['action'] == 'block_card'
        assert all(row['outcome'] == 'validated' for row in planning['validation'])
        assert not gateway.calls  # No live banking write; no customer confirmation.
