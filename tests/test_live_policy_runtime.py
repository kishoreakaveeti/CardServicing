"""HTTP-to-checkpoint policy integration, with actual vector adapter queries."""
import os
import json
import httpx
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from card_chat.catalogue import load_catalogue
from card_chat.main import create_app
from card_chat.policy.adapters.embeddings.fastembed_provider import FastEmbedEmbeddingProvider
from card_chat.policy.adapters.document_source import InMemoryDocumentSource
from card_chat.policy.adapters.retriever import HybridPolicyRetriever
from card_chat.policy.adapters.vector_stores.qdrant_store import QdrantVectorStore
from card_chat.policy.factory import build_runtime_policy_services, build_policy_retriever
from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline
from card_chat.provider import StubModel

pytestmark = pytest.mark.live_policy


@pytest.fixture
def policy_retriever(tmp_path):
    # Exercise the real Qdrant adapter in ordinary CI without Docker/model downloads.
    retriever = build_policy_retriever(
        vector_store=QdrantVectorStore(),
        config={'EMBEDDING_PROVIDER': 'deterministic', 'POLICIES_DIR': 'policies'})
    PolicyIngestionPipeline(retriever._doc_source, retriever._embedder, retriever._store,
                            audit_log_path=tmp_path / 'audit.json').ingest_all()
    yield retriever
    retriever._store.client.close()


class Gateway:
    def __init__(self):
        self.catalogue = load_catalogue()
        self.calls = []

    async def execute(self, name, arguments, key=None, **kwargs):
        self.calls.append((name, arguments, key))
        return {'ok': True, 'result': {'card_id': arguments.get('card_id'),
                                      'status': 'active' if name == 'unblock_card' else 'blocked',
                                      'last_block_reason': 'lost'}}


def application(retriever, reason='temporary freeze', status='blocked', policy_config=None):
    customer = {'sub': 'customer-a', 'username': 'synthetic.customer',
                'accounts': ['ACC-SYNTHETIC'], 'permissions': ['tools:execute'],
                'cards': [{'id': 'CARD-SYNTHETIC', 'account_number': 'ACC-SYNTHETIC',
                           'card_number': 'SIM-abcd', 'status': status, 'reason': reason}]}
    gateway = Gateway()
    app = create_app(StubModel(), gateway, policy_retriever=retriever,
                     config={'ALLOW_INSECURE_LOCAL': '1', 'LOCAL_CUSTOMER': customer,
                             **(policy_config or {})})
    return app, gateway


def checkpoint(client, app, cid):
    return client.portal.call(app.state.orchestrator.workflow.aget_state,
                              {'configurable': {'thread_id': f'isolated-test:{cid}'}}).values


@pytest.mark.parametrize('reason,offer', [('temporary freeze', True), ('permanently compromised', False)])
def test_http_issuance_uses_injected_vector_store(policy_retriever, reason, offer):
    store = policy_retriever._store
    store.search = Mock(wraps=store.search)
    store.client.query_points = Mock(wraps=store.client.query_points)
    store.initialize = Mock(side_effect=AssertionError('Runtime must not initialize the index'))
    store.upsert_chunks = Mock(side_effect=AssertionError('Runtime must not ingest policies'))
    app, gateway = application(policy_retriever, reason)
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        store.search.reset_mock()  # Exclude readiness probes.
        store.client.query_points.reset_mock()
        response = client.post(f'/conversations/{cid}/messages', json={'text': 'I want another card'})
        assert response.status_code == 200
        body = response.json()
        assert ('would you like to unblock' in body['message'].lower()) is offer
        assert not body['can_confirm']
        assert not gateway.calls
        assert store.search.call_count > 0
        assert store.client.query_points.call_count > 0
        store.initialize.assert_not_called()
        store.upsert_chunks.assert_not_called()
        state = checkpoint(client, app, cid)
        assert state['pinned_policies']['POL-001'] == '1.0.0'
        assert any(e['policy_id'] == 'POL-001' and e['version'] == '1.0.0'
                   and e['source'] for e in state['retrieved_policies'])
        assert state['policy_evaluation']['evidence_citations']
        assert app.state.orchestrator.policy_retriever is app.state.policy_retriever
        assert app.state.orchestrator.supervisor.policy_evaluator is app.state.policy_evaluator


def test_http_unblock_offer_acceptance_requires_confirmation(policy_retriever):
    app, gateway = application(policy_retriever)
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        client.post(f'/conversations/{cid}/messages', json={'text': 'I want another card'})
        ambiguous = client.post(f'/conversations/{cid}/messages', json={'text': 'maybe later'}).json()
        assert not ambiguous['can_confirm']
        assert checkpoint(client, app, cid)['pending_customer_question']
        accepted = client.post(f'/conversations/{cid}/messages', json={'text': 'yes please'}).json()
        assert accepted['can_confirm'], accepted
        assert accepted['confirmation_label'] == 'Unblock this card'
        assert not gateway.calls
        state = checkpoint(client, app, cid)
        assert state['current_handoff'].intent == 'unblock_card'
        assert state['planned_intents'] == ['issue_card']
        second = client.post(f'/conversations/{cid}/confirm', json={'review': accepted['review']}).json()
        assert second['can_confirm'], second
        assert second['review'] != accepted['review']
        assert [call[0] for call in gateway.calls] == ['unblock_card']
        completed = client.post(f'/conversations/{cid}/confirm', json={'review': second['review']}).json()
        assert 'succeeded' in completed['message'].lower()
        assert [call[0] for call in gateway.calls] == ['unblock_card', 'issue_card']


def test_http_vector_failure_blocks_issuance(policy_retriever):
    app, gateway = application(policy_retriever)
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        policy_retriever._store.search = Mock(side_effect=RuntimeError('Qdrant unavailable'))
        response = client.post(f'/conversations/{cid}/messages', json={'text': 'Issue another card'}).json()
        assert not response['can_confirm']
        state = checkpoint(client, app, cid)
        assert state['policy_evaluation']['policy_error'] == 'PolicyRetrievalUnavailable'
        assert not gateway.calls


@pytest.mark.parametrize('reason,offer', [('temporary freeze', True), ('lost and compromised', False)])
def test_http_mcp_enriches_delegated_cards_without_block_reason(policy_retriever, monkeypatch, reason, offer):
    from card_chat import mcp_server
    from mcp.shared.memory import create_connected_server_and_client_session
    from pydantic import AnyUrl

    claims = {'accounts': ['ACC-SYNTHETIC'], 'permissions': ['tools:execute'],
              'cards': [{'id': 'CARD-SYNTHETIC', 'account_number': 'ACC-SYNTHETIC', 'status': 'blocked'}]}

    class Security:
        enabled = True

        def __init__(self, config):
            pass

        def customer(self, token, audience):
            assert audience == 'mcp'
            return claims

        async def headers(self):
            return {'Authorization': 'Bearer synthetic-service-token',
                    'X-Customer-Delegation': 'synthetic-customer-assertion'}

    reads = []

    def fincrime(request):
        reads.append(request)
        assert request.url.path == '/fincrime/cards/CARD-SYNTHETIC'
        return httpx.Response(200, json={'card_id': 'CARD-SYNTHETIC', 'status': 'blocked', 'reason': reason})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(mcp_server, 'Security', Security)
    monkeypatch.setattr(mcp_server.httpx, 'AsyncClient', lambda **kw: original_client(
        transport=httpx.MockTransport(fincrime), **kw))
    server = mcp_server.create_server({'FINCRIME_API_BASE_URL': 'http://fincrime.test'})

    async def policy_facts(account_number):
        async with create_connected_server_and_client_session(server) as session:
            from mcp.shared.exceptions import McpError
            before = len(reads)
            with pytest.raises(McpError):
                await session.read_resource(AnyUrl('policy-facts://accounts/ANOTHER-CUSTOMER'))
            assert len(reads) == before
            result = await session.read_resource(AnyUrl('policy-facts://accounts/' + account_number))
            return json.loads(result.contents[0].text)

    # BFF-shaped facts have no reason; the offer must depend on the MCP/FinCrime read.
    app, gateway = application(policy_retriever, reason='')
    gateway.policy_facts = policy_facts
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        response = client.post(f'/conversations/{cid}/messages', json={'text': 'I want another card'}).json()
        assert ('would you like to unblock' in response['message'].lower()) is offer
        assert reads
        assert reads[0].headers['X-Customer-Delegation'] == 'synthetic-customer-assertion'
        assert checkpoint(client, app, cid)['verified_domain_facts']['is_temporary_freeze'] is offer
        assert not gateway.calls


def test_http_read_only_journey_does_not_require_fincrime_facts(policy_retriever):
    from unittest.mock import AsyncMock
    app, gateway = application(policy_retriever)
    gateway.policy_facts = AsyncMock(side_effect=RuntimeError('FinCrime unavailable'))
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        response = client.post(f'/conversations/{cid}/messages', json={'text': 'View my accounts'}).json()
        assert 'unavailable' not in response['message'].lower()
        gateway.policy_facts.assert_not_called()


def test_http_successful_block_offers_replacement(policy_retriever):
    app, gateway = application(policy_retriever, status='active', reason='')
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        client.post(f'/conversations/{cid}/messages', json={'text': 'I am in Australia. I lost my card, block it'})
        options = client.get(f'/conversations/{cid}/inputs').json()['tools']
        definition = next(t for t in options if t['name'] == 'block_card')
        card = next(f for f in definition['fields'] if f['name'] == 'card_id')['options'][0]['ref']
        review = client.post(f'/conversations/{cid}/inputs', json={
            'tool': 'block_card', 'fields': {'reason': 'lost'}, 'selections': {'card_id': card},
        }).json()
        assert review['can_confirm']
        assert not gateway.calls
        response = client.post(f'/conversations/{cid}/confirm', json={'review': review['review']}).json()
        assert len(gateway.calls) == 1, response
        assert 'replacement card' in response['message'].lower()
        state = checkpoint(client, app, cid)
        assert state['pending_customer_question']['policy_id'] == 'POL-003'
        assert state['pending_customer_question']['target_card_id']
        assert any(e['policy_id'] == 'POL-003' for e in state['retrieved_policies'])


@pytest.mark.parametrize('prompt', ['Please block my card and reissue a new one',
                                   'Block my card and issue a new one',
                                   'Replace my card after blocking it'])
def test_http_combined_review_blocks_then_replaces_without_second_click(policy_retriever, prompt):
    app, gateway = application(policy_retriever, status='active', reason='')
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        path = f'/conversations/{cid}'
        response = client.post(path + '/messages', json={'text': 'I am in Australia. ' + prompt}).json()
        assert response['input_tool'] == 'block_card', response
        assert checkpoint(client, app, cid)['planned_intents'] == ['replace_card']
        options = client.get(path + '/inputs').json()['tools']
        definition = next(t for t in options if t['name'] == 'block_card')
        ref = next(f for f in definition['fields'] if f['name'] == 'card_id')['options'][0]['ref']
        review = client.post(path + '/inputs', json={'tool': 'block_card',
            'fields': {'reason': 'lost'}, 'selections': {'card_id': ref}}).json()
        assert review['confirmation_label'] == 'Block and replace this card'
        assert 'also authorizes' in review['message']
        assert not gateway.calls
        result = client.post(path + '/confirm', json={'review': review['review']},
                             headers={'Idempotency-Key': 'combined-confirmation'}).json()
        assert [call[0] for call in gateway.calls] == ['block_card', 'replace_card'], result
        assert gateway.calls[0][1]['card_id'] == gateway.calls[1][1]['card_id']
        assert not result['can_confirm']
        replay = client.post(path + '/confirm', json={'review': review['review']},
                             headers={'Idempotency-Key': 'combined-confirmation'}).json()
        assert replay == result
        assert len(gateway.calls) == 2
        state = checkpoint(client, app, cid)
        assert all(step['status'] == 'completed' for step in state['action_plan'])


def test_http_combined_review_does_not_replace_after_failed_block(policy_retriever):
    app, gateway = application(policy_retriever, status='active', reason='')
    async def fail(name, arguments, key=None, **kwargs):
        gateway.calls.append((name, arguments, key))
        return {'ok': False, 'error': 'Block failed'}
    gateway.execute = fail
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        path = f'/conversations/{cid}'
        client.post(path + '/messages', json={'text': 'Block my card and reissue a new one'})
        options = client.get(path + '/inputs').json()['tools']
        definition = next(t for t in options if t['name'] == 'block_card')
        ref = next(f for f in definition['fields'] if f['name'] == 'card_id')['options'][0]['ref']
        review = client.post(path + '/inputs', json={'tool': 'block_card',
            'fields': {'reason': 'lost'}, 'selections': {'card_id': ref}}).json()
        result = client.post(path + '/confirm', json={'review': review['review']}).json()
        assert [call[0] for call in gateway.calls] == ['block_card']
        assert 'failed' in result['message'].lower()


def test_startup_requires_remote_policy_infrastructure():
    with pytest.raises(RuntimeError, match='persistent QDRANT_URL'):
        build_runtime_policy_services({'QDRANT_URL': ':memory:'})


@pytest.mark.parametrize('fault', ['missing', 'stale_version', 'stale_content'])
def test_startup_rejects_missing_or_stale_index(policy_retriever, fault):
    if fault == 'missing':
        policy_retriever._store.delete_by_policy_id('POL-001')
    else:
        documents = policy_retriever._doc_source.list_documents()
        update = {'version': '2.0.0'} if fault == 'stale_version' else {'content_hash': 'changed'}
        policy_retriever._doc_source = InMemoryDocumentSource([
            doc.model_copy(update=update) if doc.policy_id == 'POL-001' else doc
            for doc in documents])
    with pytest.raises(RuntimeError, match='Approved policy index unavailable'):
        build_runtime_policy_services({}, retriever=policy_retriever)


def test_http_block_preserves_security_action_during_vector_outage(policy_retriever):
    app, gateway = application(policy_retriever, status='active', reason='')
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        options = client.get(f'/conversations/{cid}/inputs').json()['tools']
        definition = next(t for t in options if t['name'] == 'block_card')
        card = next(f for f in definition['fields'] if f['name'] == 'card_id')['options'][0]['ref']
        review = client.post(f'/conversations/{cid}/inputs', json={
            'tool': 'block_card', 'fields': {'reason': 'lost'}, 'selections': {'card_id': card},
        }).json()
        policy_retriever._store.search = Mock(side_effect=RuntimeError('Qdrant unavailable'))
        response = client.post(f'/conversations/{cid}/confirm', json={'review': review['review']}).json()
        assert 'unavailable' in response['message']
        assert [call[0] for call in gateway.calls] == ['block_card']


def test_docker_qdrant_fastembed_http_customer_request(policy_retriever, tmp_path):
    """Opt in with POLICY_TEST_QDRANT_URL pointing to a Docker Qdrant service."""
    url = os.getenv('POLICY_TEST_QDRANT_URL')
    if not url:
        pytest.skip('Set POLICY_TEST_QDRANT_URL to run real Qdrant/FastEmbed integration')
    store = QdrantVectorStore(url=url, collection_name='policy_test_' + uuid4().hex)
    embedder = FastEmbedEmbeddingProvider()
    retriever = HybridPolicyRetriever(policy_retriever._doc_source, embedder, store)
    try:
        PolicyIngestionPipeline(retriever._doc_source, embedder, store,
                                audit_log_path=tmp_path / 'real-audit.json').ingest_all()
        # No retriever override: exercise production FastAPI factory composition.
        app, gateway = application(None, policy_config={
            'QDRANT_URL': url, 'POLICY_COLLECTION': store.collection_name,
            'EMBEDDING_PROVIDER': 'fastembed', 'POLICY_VECTOR_STORE': 'qdrant',
            'POLICIES_DIR': 'policies', 'FASTEMBED_MODEL': embedder.model_name,
        })
        with TestClient(app) as client:
            live_store = app.state.policy_retriever._store
            live_store.client.query_points = Mock(wraps=live_store.client.query_points)
            cid = client.post('/conversations').json()['id']
            response = client.post(f'/conversations/{cid}/messages',
                                   json={'text': 'I want another card'}).json()
            assert 'would you like to unblock' in response['message'].lower()
            assert live_store.client.query_points.call_count > 0
            assert checkpoint(client, app, cid)['pinned_policies']['POL-001'] == '1.0.0'
            assert not gateway.calls
            live_store.client.close()
    finally:
        store.client.delete_collection(store.collection_name)
        store.client.close()
