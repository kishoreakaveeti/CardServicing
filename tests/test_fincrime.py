"""Unit and integration tests for FinCrime Agent, service, database sync, and cross-agent orchestration."""
import sqlite3
import pytest
from uuid import uuid4
from fastapi import HTTPException
from fastapi.testclient import TestClient

from card_servicing.models import CardCreate
from card_servicing.persistence import SQLiteCardStore
from card_servicing.banking import BankingService
from card_servicing.idempotency import IdempotencyConflict
from fincrime_servicing.models import CardBlockRequest
from fincrime_servicing.persistence import SQLiteFinCrimeStore
from fincrime_servicing.service import FinCrimeService
from fincrime_servicing.main import create_app as fincrime_app
from card_chat.agent import CardServicingAgent, FinCrimeAgent, AgentOrchestrator
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.provider import StubModel
from card_chat.main import create_app as agent_app
from card_chat.catalogue import load_catalogue
from card_chat.security import Security, delegation
from test_chat import prepare_review


# ==========================================
# 1. FinCrime Persistence & Audit Log Tests
# ==========================================

def test_fincrime_persistence_and_audit(tmp_path):
    db_path = tmp_path / "fincrime.db"
    store = SQLiteFinCrimeStore(str(db_path))
    store.initialize()

    # Block card
    card_id = uuid4().hex
    cust_id = "customer-1"
    account_num = "ACC-1001"
    res1, replayed1 = store.block_card_idempotent(cust_id, card_id, account_num, "lost card", "key-1")
    assert not replayed1
    assert res1.status == "blocked"
    assert res1.synced is True

    # Idempotent replay with same key
    res2, replayed2 = store.block_card_idempotent(cust_id, card_id, account_num, "lost card", "key-1")
    assert replayed2
    assert res2.card_id == card_id

    # Conflict replay with different reason
    with pytest.raises(IdempotencyConflict):
        store.block_card_idempotent(cust_id, card_id, account_num, "stolen card", "key-1")

    # Account block check
    assert store.is_account_blocked(account_num) is True
    assert store.is_account_blocked("ACC-OTHER") is False

    # Check audit log
    with store.connect() as db:
        logs = db.execute("SELECT event, customer_id, card_id, details FROM fincrime_audit_log").fetchall()
        assert len(logs) == 1
        assert logs[0][0] == "card_blocked"
        assert logs[0][1] == cust_id
        assert logs[0][2] == card_id
        assert "Reason: lost card" in logs[0][3]


# ==========================================
# 2. Service & Database Sync Tests
# ==========================================

@pytest.mark.anyio
async def test_fincrime_service_sync_and_auth(tmp_path):
    cards_db = tmp_path / "cards.db"
    fincrime_db = tmp_path / "fincrime.db"

    card_store = SQLiteCardStore(str(cards_db))
    card_store.initialize()
    banking = BankingService(card_store)

    fincrime_store = SQLiteFinCrimeStore(str(fincrime_db))
    fincrime_store.initialize()
    fincrime_svc = FinCrimeService(fincrime_store, cards_service=banking)

    # Register user and create account + card
    cust1 = banking.register(type('Creds', (), {'username': 'alice', 'password': 'password-12345'})())
    cust1['username'] = 'alice'
    account = banking.create_account(cust1, type('Acc', (), {'account_type': 'savings', 'issue_card': True, 'model_dump_json': lambda s: '{}'})(), "acc-key-1")
    cards = banking.cards(cust1['sub'])
    assert len(cards) == 1
    alice_card = cards[0]
    assert alice_card.status == "created"

    # Unauthorized customer cannot block Alice's card
    cust2 = {'sub': 'bob-id', 'username': 'bob'}
    with pytest.raises(HTTPException) as exc:
        await fincrime_svc.block_card(cust2, str(alice_card.id), "lost card")
    assert exc.value.status_code == 403

    # Alice blocks her card
    res, replayed = await fincrime_svc.block_card(cust1, str(alice_card.id), "lost card", key="block-1")
    assert res.status == "blocked"
    assert res.synced is True

    # Verify status in Cards database is synchronized to 'blocked'
    updated_card = card_store.get_card(str(alice_card.id))
    assert updated_card.status == "blocked"

    # FinCrime database confirms account is blocked
    assert fincrime_svc.is_account_blocked(alice_card.account_number) is True


# ==========================================
# 3. Cross-Agent Dependency: Issuance Rejected
# ==========================================

def test_cross_agent_issuance_rejection_on_blocked_card(tmp_path):
    cards_db = tmp_path / "cards.db"
    card_store = SQLiteCardStore(str(cards_db))
    card_store.initialize()
    banking = BankingService(card_store)

    # Register and create account
    cust = banking.register(type('Creds', (), {'username': 'charlie', 'password': 'password-12345'})())
    cust['username'] = 'charlie'
    account = banking.create_account(cust, type('Acc', (), {'account_type': 'savings', 'issue_card': True, 'model_dump_json': lambda s: '{}'})(), "acc-key-charlie")
    card = banking.cards(cust['sub'])[0]

    # Block the card directly in the store
    card_store.update_card_status(str(card.id), "blocked")
    assert card_store.has_blocked_cards_for_account(account['account_number']) is True

    # Attempting to issue a new additional card on this account must fail with 400
    with pytest.raises(HTTPException) as exc:
        banking.issue(cust, account['account_number'], "new-card-key", additional=True)
    assert exc.value.status_code == 400
    assert "blocked" in exc.value.detail.lower()


# ==========================================
# 4. Multi-Agent Orchestrator Routing
# ==========================================

@pytest.mark.anyio
async def test_agent_orchestrator_routing():
    model = StubModel()
    cards_agent = CardServicingAgent(model)
    fincrime_agent = FinCrimeAgent(model)

    async def mock_check_blocked(acc):
        return 'blocked' in str(acc).lower()

    orchestrator = AgentOrchestrator(model, cards_agent, fincrime_agent, check_blocked_fn=mock_check_blocked)

    # 1. Routing to FinCrime on card block intent
    route1 = orchestrator.route([{'role': 'user', 'content': 'Please block my card'}], {})
    assert route1 == "fincrime"

    route2 = orchestrator.route([{'role': 'user', 'content': 'I lost my card, please freeze it'}], {})
    assert route2 == "fincrime"

    # 2. Routing to Cards Agent on banking intent
    route3 = orchestrator.route([{'role': 'user', 'content': 'I want to view my accounts'}], {})
    assert route3 == "cards"

    route4 = orchestrator.route([{'role': 'user', 'content': 'Issue an additional card'}], {})
    assert route4 == "cards"

    # 3. FinCrime turn interpretation
    tools = {
        'block_card': {'name': 'block_card', 'description': 'Block a card', 'input_schema': {'type': 'object', 'properties': {'card_id': {'type': 'string'}}}},
        'issue_card': {'name': 'issue_card', 'description': 'Issue a card', 'input_schema': {'type': 'object', 'properties': {'account_number': {'type': 'string'}}}},
        'view_cards': {'name': 'view_cards', 'description': 'View cards', 'input_schema': {'type': 'object', 'properties': {}}}
    }
    fincrime_turn = await orchestrator.interpret_turn(
        [{'role': 'user', 'content': 'Block my card'}],
        {},
        tools
    )
    assert isinstance(fincrime_turn, ProposedToolCall)
    assert fincrime_turn.name == "block_card"

    # 4. Cross-agent pre-issuance blocking interception
    ref_blocked = 'selected_account_blocked' + 'a' * 16
    cards_turn = await orchestrator.interpret_turn(
        [{'role': 'user', 'content': 'Issue a card'}],
        {'tool': 'issue_card', 'arguments': {'account_number': ref_blocked}},
        tools
    )
    assert isinstance(cards_turn, CustomerReply)
    assert "blocked" in cards_turn.text.lower()


# ==========================================
# 5. End-to-End Chat & Conversational Blocking
# ==========================================

def test_conversational_block_and_subsequent_issuance_blocked(tmp_path, monkeypatch):
    cards_db = tmp_path / "cards.db"
    fincrime_db = tmp_path / "fincrime.db"

    card_store = SQLiteCardStore(str(cards_db))
    card_store.initialize()
    banking = BankingService(card_store)

    fincrime_store = SQLiteFinCrimeStore(str(fincrime_db))
    fincrime_store.initialize()
    fincrime_svc = FinCrimeService(fincrime_store, cards_service=banking)

    # Create customer and issue initial card
    cust = banking.register(type('Creds', (), {'username': 'dan', 'password': 'password-12345'})())
    cust['username'] = 'dan'
    account = banking.create_account(cust, type('Acc', (), {'account_type': 'transaction', 'issue_card': True, 'model_dump_json': lambda s: '{}'})(), "acc-dan")
    card = banking.cards(cust['sub'])[0]
    last4 = card.card_number[-4:]

    # Local customer context simulating signed delegation with customer cards
    local_cust = {
        'sub': cust['sub'],
        'username': 'dan',
        'accounts': [account['account_number']],
        'cards': [{'id': str(card.id), 'card_number': card.card_number, 'status': card.status, 'account_number': account['account_number']}],
        'permissions': ['tools:execute']
    }

    cat = load_catalogue()
    class Gateway:
        def __init__(self):
            self.catalogue = cat
            self.tools = cat
        async def execute(self, name, arguments, key=None):
            if name == 'block_card':
                res = banking.block_card(cust, arguments['card_id'], arguments.get('reason', 'Customer requested'))
                return {'ok': True, 'result': res}
            elif name == 'issue_card':
                res, _ = banking.issue(cust, arguments['account_number'], key or "test-key", additional=True, with_replay=True)
                return {'ok': True, 'result': res.model_dump()}
            return {'ok': False, 'error': 'Unknown'}

    model = StubModel()
    config = {
        'LOCAL_CUSTOMER': local_cust,
        'AGENT_DB': str(tmp_path / 'agent.db'),
        'ALLOW_INSECURE_LOCAL': '1'
    }

    app = agent_app(model, Gateway(), config=config)
    app.state.fincrime_service = fincrime_svc

    with TestClient(app) as client:
        # Start conversation
        cid = client.post('/conversations').json()['id']
        path = f'/conversations/{cid}'

        # Conversational request 1: "Block my card" (generic, opens secure form)
        resp_generic = client.post(path + '/messages', json={'text': 'Block my card'}).json()
        assert resp_generic['input_tool'] == 'block_card'

        # Conversational request 2: "Block my card ending in <last4>" (resolves card directly to review)
        resp = client.post(path + '/messages', json={'text': f'Block my card ending in {last4}'}).json()
        assert resp['can_confirm'] is True
        assert resp['confirmation_label'] == 'Block this card'

        # Confirm card blocking
        confirm_resp = client.post(path + '/confirm', json={'review': resp['review']}).json()
        assert "succeeded" in confirm_resp['message'].lower()
        assert "blocked" in confirm_resp['message'].lower()

        # Update local customer card status to reflect blocked card
        local_cust['cards'][0]['status'] = 'blocked'

        # In a new conversation, try to request a new card on the same account
        cid2 = client.post('/conversations').json()['id']
        path2 = f'/conversations/{cid2}'
        resp_issue = client.post(path2 + '/messages', json={'text': 'I need a new card'}).json()
        assert resp_issue['can_confirm'] is False
        assert 'blocked' in resp_issue['message'].lower()

        # When attempting to submit the form for this account, it is blocked
        resp_blocked = prepare_review(client, cid2, {'account_number': account['account_number']}, tool='issue_card').json()
        assert "blocked" in resp_blocked['message'].lower()
        assert resp_blocked['can_confirm'] is False


# ==========================================
# 6. Agent-Specific Tool Isolation & MCP Access Control
# ==========================================

@pytest.mark.anyio
async def test_agent_tool_isolation_in_orchestrator():
    """Verify cards_agent only sees card tools and fincrime_agent only sees FinCrime tools."""
    class InspectingModel(StubModel):
        def __init__(self):
            super().__init__()
            self.last_seen_tools = {}

        async def turn(self, history, collected, tools):
            self.last_seen_tools = dict(tools)
            if 'block_card' in tools:
                return ProposedToolCall('block_card', {'card_id': 'c-1', 'reason': 'fraud'})
            return ProposedToolCall('view_cards', {})

    model = InspectingModel()
    cards_agent = CardServicingAgent(model)
    fincrime_agent = FinCrimeAgent(model)
    orchestrator = AgentOrchestrator(model, cards_agent, fincrime_agent)

    all_tools = {
        'create_card': {'name': 'create_card', 'description': 'create'},
        'issue_card': {'name': 'issue_card', 'description': 'issue'},
        'view_cards': {'name': 'view_cards', 'description': 'view'},
        'view_accounts': {'name': 'view_accounts', 'description': 'accounts'},
        'create_account': {'name': 'create_account', 'description': 'create account'},
        'block_card': {'name': 'block_card', 'description': 'block'},
        'logout': {'name': 'logout', 'description': 'logout'}
    }

    # FinCrime turn: prompt 'block my card'
    await orchestrator.interpret_turn([{'role': 'user', 'content': 'Block my card'}], {}, all_tools)
    # FinCrime agent must see ONLY FinCrime tools (block_card) + logout
    assert set(model.last_seen_tools.keys()) == {'block_card', 'logout'}
    assert 'create_card' not in model.last_seen_tools
    assert 'issue_card' not in model.last_seen_tools
    assert 'view_accounts' not in model.last_seen_tools

    # Cards turn: prompt 'show my accounts'
    await orchestrator.interpret_turn([{'role': 'user', 'content': 'Show my accounts'}], {}, all_tools)
    # Cards agent must see ONLY card servicing tools + logout, never block_card
    assert 'block_card' not in model.last_seen_tools
    assert {'create_card', 'issue_card', 'view_cards', 'view_accounts', 'create_account', 'logout'}.issubset(
        set(model.last_seen_tools.keys())
    )


def test_mcp_agent_tools_endpoint_and_policy():
    """Verify MCP endpoint /mcp/agents/{agent_name}/tools and tool policy configuration."""
    from card_chat.mcp_server import create_app as mcp_app, get_agent_tools_policy
    config = {'ALLOW_INSECURE_LOCAL': '1'}
    policy = get_agent_tools_policy(config)

    assert 'cards_agent' in policy
    assert 'fincrime_agent' in policy
    assert 'block_card' in policy['fincrime_agent']
    assert 'block_card' not in policy['cards_agent']
    assert 'create_card' in policy['cards_agent']
    assert 'create_card' not in policy['fincrime_agent']

    with TestClient(mcp_app(config)) as client:
        # Check tools for fincrime_agent
        fc_resp = client.get('/mcp/agents/fincrime_agent/tools')
        assert fc_resp.status_code == 200
        fc_tools = {t['name'] for t in fc_resp.json()['tools']}
        assert fc_tools == {'block_card', 'unblock_card'}

        # Check tools for cards_agent
        card_resp = client.get('/mcp/agents/cards_agent/tools')
        assert card_resp.status_code == 200
        card_tools = {t['name'] for t in card_resp.json()['tools']}
        assert 'block_card' not in card_tools
        assert 'unblock_card' not in card_tools
        assert {'create_card', 'issue_card', 'view_accounts', 'view_cards', 'create_account'}.issubset(card_tools)


@pytest.mark.anyio
async def test_mcp_server_enforces_agent_tool_policy():
    """Verify MCP server rejects tool calls with HTTP 403 when an agent tries to call an unpermitted tool."""
    import mcp.types
    from card_chat.mcp_server import create_server
    from mcp.server.lowlevel.server import request_ctx
    from unittest.mock import MagicMock

    config = {'ALLOW_INSECURE_LOCAL': '1'}
    server = create_server(config)
    call_handler = server.request_handlers[mcp.types.CallToolRequest]

    # 1. fincrime_agent calling create_card -> 403 error
    mock_ctx_fc = MagicMock()
    mock_ctx_fc.meta.model_dump.return_value = {'agent': 'fincrime_agent'}
    tok1 = request_ctx.set(mock_ctx_fc)
    try:
        req1 = mcp.types.CallToolRequest(params=mcp.types.CallToolRequestParams(name='create_card', arguments={}))
        res1 = await call_handler(req1)
        res1_obj = getattr(res1, 'root', res1)
        assert res1_obj.isError is True
        assert "not permitted for agent 'fincrime_agent'" in res1_obj.content[0].text
    finally:
        request_ctx.reset(tok1)

    # 2. cards_agent calling block_card -> 403 error
    mock_ctx_ca = MagicMock()
    mock_ctx_ca.meta.model_dump.return_value = {'agent': 'cards_agent'}
    tok2 = request_ctx.set(mock_ctx_ca)
    try:
        req2 = mcp.types.CallToolRequest(params=mcp.types.CallToolRequestParams(name='block_card', arguments={'card_id': 'c-1'}))
        res2 = await call_handler(req2)
        res2_obj = getattr(res2, 'root', res2)
        assert res2_obj.isError is True
        assert "not permitted for agent 'cards_agent'" in res2_obj.content[0].text
    finally:
        request_ctx.reset(tok2)

    # 3. cards_agent calling unblock_card -> 403 error
    tok3 = request_ctx.set(mock_ctx_ca)
    try:
        req3 = mcp.types.CallToolRequest(params=mcp.types.CallToolRequestParams(name='unblock_card', arguments={'card_id': 'c-1'}))
        res3 = await call_handler(req3)
        res3_obj = getattr(res3, 'root', res3)
        assert res3_obj.isError is True
        assert "not permitted for agent 'cards_agent'" in res3_obj.content[0].text
    finally:
        request_ctx.reset(tok3)


# ==========================================
# 7. Card Unblocking Tests
# ==========================================

@pytest.mark.anyio
async def test_card_unblocking_service_and_reissuance(tmp_path):
    """Verify card unblocking lifecycle: store, audit, validation, and account re-enabling."""
    cards_db = tmp_path / "cards.db"
    fincrime_db = tmp_path / "fincrime.db"

    card_store = SQLiteCardStore(str(cards_db))
    card_store.initialize()
    banking = BankingService(card_store)

    fincrime_store = SQLiteFinCrimeStore(str(fincrime_db))
    fincrime_store.initialize()
    fincrime_svc = FinCrimeService(fincrime_store, cards_service=banking)

    # Register and create account with card
    cust = banking.register(type('Creds', (), {'username': 'eve', 'password': 'password-12345'})())
    cust['username'] = 'eve'
    account = banking.create_account(cust, type('Acc', (), {'account_type': 'savings', 'issue_card': True, 'model_dump_json': lambda s: '{}'})(), "acc-eve")
    card = banking.cards(cust['sub'])[0]

    # Attempting to unblock an active (unblocked) card must fail with 400
    with pytest.raises(HTTPException) as exc_unblocked:
        await fincrime_svc.unblock_card(cust, str(card.id), "trying to unblock active card")
    assert exc_unblocked.value.status_code == 400
    assert "only blocked cards" in exc_unblocked.value.detail.lower()

    # Block the card
    await fincrime_svc.block_card(cust, str(card.id), "suspicious activity")
    assert card_store.get_card(str(card.id)).status == "blocked"
    assert fincrime_svc.is_account_blocked(account['account_number']) is True

    # Account issuance is blocked
    with pytest.raises(HTTPException) as exc_issue:
        banking.issue(cust, account['account_number'], "attempt-while-blocked", additional=True)
    assert exc_issue.value.status_code == 400

    # Unblock the card
    unblock_res, _ = await fincrime_svc.unblock_card(cust, str(card.id), "false alarm verified", key="unblock-key-1")
    assert unblock_res.status == "created"
    assert card_store.get_card(str(card.id)).status == "created"
    assert fincrime_svc.is_account_blocked(account['account_number']) is False

    # Check audit log in FinCrime store contains card_unblocked event
    with fincrime_store.connect() as db:
        events = [r[0] for r in db.execute("SELECT event FROM fincrime_audit_log WHERE card_id=?", (str(card.id),)).fetchall()]
        assert "card_blocked" in events
        assert "card_unblocked" in events

    # Now card issuance on this account is permitted again!
    new_card = banking.issue(cust, account['account_number'], "post-unblock-issue", additional=True)
    assert new_card.account_number == account['account_number']
    assert new_card.status == "created"


def test_conversational_unblock_journey(tmp_path):
    """Verify chat journey for unblocking a card with additional confirmation warning prompt."""
    from card_chat.private_inputs import form_options

    cards_db = tmp_path / "cards.db"
    fincrime_db = tmp_path / "fincrime.db"

    card_store = SQLiteCardStore(str(cards_db))
    card_store.initialize()
    banking = BankingService(card_store)

    fincrime_store = SQLiteFinCrimeStore(str(fincrime_db))
    fincrime_store.initialize()
    fincrime_svc = FinCrimeService(fincrime_store, cards_service=banking)

    cust = banking.register(type('Creds', (), {'username': 'frank', 'password': 'password-12345'})())
    cust['username'] = 'frank'
    account = banking.create_account(cust, type('Acc', (), {'account_type': 'transaction', 'issue_card': True, 'model_dump_json': lambda s: '{}'})(), "acc-frank")
    card = banking.cards(cust['sub'])[0]
    last4 = card.card_number[-4:]

    # Initially card is active (created)
    local_cust = {
        'sub': cust['sub'],
        'username': 'frank',
        'accounts': [account['account_number']],
        'cards': [{'id': str(card.id), 'card_number': card.card_number, 'status': 'created', 'account_number': account['account_number']}],
        'permissions': ['tools:execute']
    }

    cat = load_catalogue()
    bindings = {}
    options_before = form_options(cat, local_cust, bindings, 'cid-1', cust['sub'])
    unblock_opts_before = next(t for t in options_before['tools'] if t['name'] == 'unblock_card')
    card_id_opts_before = next(f for f in unblock_opts_before['fields'] if f['name'] == 'card_id')
    # Since card is NOT blocked, unblock_card must offer 0 options!
    assert len(card_id_opts_before['options']) == 0

    # Now card is blocked
    banking.block_card(cust, str(card.id), "lost")
    local_cust['cards'][0]['status'] = 'blocked'

    # form_options must now offer this card for unblock_card!
    options_after = form_options(cat, local_cust, bindings, 'cid-1', cust['sub'])
    unblock_opts_after = next(t for t in options_after['tools'] if t['name'] == 'unblock_card')
    card_id_opts_after = next(f for f in unblock_opts_after['fields'] if f['name'] == 'card_id')
    assert len(card_id_opts_after['options']) == 1

    class Gateway:
        def __init__(self):
            self.catalogue = cat
            self.tools = cat
        async def execute(self, name, arguments, key=None):
            if name == 'unblock_card':
                res = banking.unblock_card(cust, arguments['card_id'], arguments.get('reason', 'Customer requested unblock'))
                return {'ok': True, 'result': res}
            return {'ok': False, 'error': 'Unknown'}

    model = StubModel()
    config = {
        'LOCAL_CUSTOMER': local_cust,
        'AGENT_DB': str(tmp_path / 'agent.db'),
        'ALLOW_INSECURE_LOCAL': '1'
    }

    app = agent_app(model, Gateway(), config=config)
    app.state.fincrime_service = fincrime_svc

    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        path = f'/conversations/{cid}'

        # Conversational request: "Unblock my card ending in <last4>"
        resp = client.post(path + '/messages', json={'text': f'Please unblock my card ending in {last4}'}).json()
        assert resp['can_confirm'] is True
        assert resp['confirmation_label'] == 'Unblock this card'
        # Additional prompting warning is presented to the user
        assert 'ATTENTION: Unblocking this card will restore its active status' in resp['message']

        # Confirm unblocking
        confirm_resp = client.post(path + '/confirm', json={'review': resp['review']}).json()
        assert "succeeded" in confirm_resp['message'].lower()
        assert "created" in confirm_resp['message'].lower() or "unblocked" in confirm_resp['message'].lower()


def test_conversational_i_lost_my_card_with_remote_fincrime_agent(tmp_path):
    from card_chat.fincrime_agent import create_app as create_remote_fincrime_app, RemoteFinCrimeAgent
    from card_chat.private_inputs import form_options

    cat = load_catalogue({'CARD_API_BASE_URL': 'http://test'})
    cust = {'sub': 'customer-lost-test', 'permissions': ['tools:execute']}
    card_id = str(uuid4())
    local_cust = {
        'sub': cust['sub'],
        'username': 'lost_tester',
        'permissions': ['tools:execute'],
        'accounts': ['ACC-LOST-1'],
        'cards': [{'id': card_id, 'card_number': '4111111111119999', 'account_number': 'ACC-LOST-1', 'status': 'created'}]
    }

    # Set up remote fincrime agent app
    remote_app = create_remote_fincrime_app(StubModel(), config={'ALLOW_INSECURE_LOCAL': '1'})
    with TestClient(remote_app) as remote_client:
        # RemoteFinCrimeAgent that delegates via testclient
        class TestRemoteFinCrimeAgent(RemoteFinCrimeAgent):
            async def interpret_turn(self, history: list, collected: dict, allowed_tools: dict):
                resp = remote_client.post('/turn', json={
                    'history': history,
                    'collected': collected,
                    'allowed_tools': allowed_tools
                })
                assert resp.status_code == 200
                data = resp.json()
                if data.get('kind') == 'reply':
                    return CustomerReply(data.get('text', ''))
                elif data.get('kind') == 'proposal':
                    return ProposedToolCall(data.get('name'), data.get('arguments', {}))
                return None

        class Gateway:
            def __init__(self):
                self.catalogue = cat
                self.tools = cat
            async def execute(self, name, arguments, key=None, agent=None):
                return {'ok': True, 'result': {'card_id': arguments.get('card_id'), 'status': 'blocked'}}

        config = {
            'LOCAL_CUSTOMER': local_cust,
            'AGENT_DB': str(tmp_path / 'agent.db'),
            'ALLOW_INSECURE_LOCAL': '1'
        }

        app = agent_app(StubModel(), Gateway(), config=config)

        with TestClient(app) as client:
            client.app.state.orchestrator.fincrime_agent = TestRemoteFinCrimeAgent('http://fake-remote')
            cid = client.post('/conversations').json()['id']
            path = f'/conversations/{cid}'

            # Conversational request: "i lost my card" (generic intent, opens secure form)
            resp = client.post(path + '/messages', json={'text': 'i lost my card'}).json()
            assert resp['input_tool'] == 'block_card'
            assert 'block card' in resp['message'].lower()




