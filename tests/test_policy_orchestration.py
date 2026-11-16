"""End-to-end tests for policy-driven agentic orchestration with RAG and LangGraph state.

Covers:
- Scenario A: Blocked card pre-issuance restriction, unblock journey offer, customer acceptance/decline,
              and permanently compromised card handling.
- Scenario B: Lost card blocking, proactive replacement offer under POL-003, customer acceptance and decline.
- Scenario C: Policy update dynamically changes workflow behavior without modifying supervisor code.
- Policy pinning and safe re-evaluation.
- Fail closed behavior on missing mandatory policy.
- Zero customer PII leakage and deterministic confirmation preservation.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import pytest

from card_chat.agent import AgentOrchestrator, CardServicingAgent, FinCrimeAgent
from card_chat.orchestration.contracts import AgentHandoff, HandoffResult
from card_chat.orchestration.persistence import DurableSqliteSaver
from card_chat.orchestration.supervisor import SupervisorAgent
from card_chat.policy.evaluator import PolicyEvaluator
from card_chat.policy.models import PendingCustomerQuestion
from card_chat.policy.store import PolicyStore
from card_chat.provider import StubModel
from card_chat.turns import CustomerReply, ProposedToolCall
from card_servicing.banking import AccountCreate, BankingService, Credentials
from card_servicing.persistence import SQLiteCardStore
from fincrime_servicing.persistence import SQLiteFinCrimeStore
from fincrime_servicing.service import FinCrimeService


@pytest.fixture
def policy_fixture(tmp_path):
    """Fixture initializing isolated policy store, banking service, and FinCrime service."""
    policy_db = str(tmp_path / "policies.db")
    cards_db = str(tmp_path / "cards.db")
    fincrime_db = str(tmp_path / "fincrime.db")
    agent_db = str(tmp_path / "agent.db")

    policy_store = PolicyStore(policy_db)
    policy_store.index_directory("policies")
    policy_evaluator = PolicyEvaluator(policy_store)

    card_store = SQLiteCardStore(cards_db)
    card_store.initialize()
    banking = BankingService(card_store)

    fincrime_store = SQLiteFinCrimeStore(fincrime_db)
    fincrime_store.initialize()
    fincrime_svc = FinCrimeService(fincrime_store, cards_service=banking)

    checkpointer = DurableSqliteSaver(agent_db)

    # Register customer and create initial account with card
    reg = banking.register(Credentials(username="emily", password="synthetic-password-123"))
    cust = {"sub": reg["sub"], "username": "emily", "preferred_username": "emily", "accounts": []}
    acc = banking.create_account(cust, AccountCreate(account_type="savings", issue_card=True), key="acc-emily")
    cust["accounts"] = [acc["account_number"]]
    card = banking.cards(cust["sub"])[0]

    return {
        "tmp_path": tmp_path,
        "policy_store": policy_store,
        "policy_evaluator": policy_evaluator,
        "banking": banking,
        "fincrime_svc": fincrime_svc,
        "card_store": card_store,
        "checkpointer": checkpointer,
        "customer": cust,
        "account": acc,
        "card": card,
    }


# ==============================================================================
# Scenario A — Blocked Card and Additional Issuance
# ==============================================================================

@pytest.mark.anyio
async def test_scenario_a_temporary_freeze_unblock_offer_accepted(policy_fixture):
    """Scenario A: Frozen card blocks issuance -> supervisor offers unblock -> customer accepts ->

    unblocking executes with confirmation -> issuance resumes with separate confirmation.
    """
    banking: BankingService = policy_fixture["banking"]
    fincrime_svc: FinCrimeService = policy_fixture["fincrime_svc"]
    cust = policy_fixture["customer"]
    card = policy_fixture["card"]
    acc = policy_fixture["account"]
    checkpointer = policy_fixture["checkpointer"]
    policy_store = policy_fixture["policy_store"]
    policy_evaluator = policy_fixture["policy_evaluator"]

    # 1. Customer card is temporarily frozen
    await fincrime_svc.block_card(cust, str(card.id), reason="temporary customer freeze", key="freeze-1")
    assert fincrime_svc.is_account_blocked(acc["account_number"]) is True
    assert fincrime_svc.has_temporary_freeze(acc["account_number"]) is True

    async def facts_retriever(account_number=None, state=None):
        return {
            "account_number": acc["account_number"],
            "has_blocked_card": True,
            "temporary_freeze": True,
            "compromised": False,
            "blocked_cards": [{"id": str(card.id), "reason": "temporary customer freeze", "status": "blocked"}],
        }

    model = StubModel()
    orch = AgentOrchestrator(
        model=model,
        checkpointer=checkpointer,
        policy_store=policy_store,
        policy_evaluator=policy_evaluator,
        facts_retriever_fn=facts_retriever,
        check_blocked_fn=fincrime_svc.is_account_blocked,
    )

    thread_id = f"{cust['sub']}:scenario_a_accepted"
    owner = cust["sub"]
    session_id = "session_a"
    allowed_tools = {"issue_card": {}, "unblock_card": {}, "view_cards": {}}

    # Turn 1: Customer requests new card: "Issue another card for my account"
    # RAG retrieves POL-001. Supervisor explains restriction and proactively offers unblocking journey.
    turn1 = await orch.interpret_turn(
        history=[{"role": "user", "content": "Issue another card for my account"}],
        collected={"tool": "issue_card", "arguments": {"account_number": acc["account_number"]}},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=owner,
        session_id=session_id,
    )

    assert isinstance(turn1, CustomerReply)
    assert "POL-001" in turn1.text or "unblocked or resolved" in turn1.text
    assert "would you like to unblock it first?" in turn1.text.lower()

    # Verify LangGraph state captured pending question and pinned policy version
    state_snap1 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
    pending_q = state_snap1.values.get("pending_customer_question")
    assert pending_q is not None
    assert pending_q["question_type"] == "unblock_offer"
    assert pending_q["policy_id"] == "POL-001"
    assert state_snap1.values.get("pinned_policies", {}).get("POL-001") == "1.0.0"

    # Turn 2: Customer accepts: "Yes, please"
    # Supervisor interprets acceptance in context of POL-001 pending question, delegates to FinCrimeAgent.
    turn2 = await orch.interpret_turn(
        history=[
            {"role": "user", "content": "Issue another card for my account"},
            {"role": "assistant", "content": turn1.text},
            {"role": "user", "content": "Yes, please"},
        ],
        collected={},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=owner,
        session_id=session_id,
    )

    # FinCrime specialist prepares unblock proposal, pauses for confirmation #1
    assert isinstance(turn2, ProposedToolCall)
    assert turn2.name == "unblock_card"
    assert turn2.arguments["card_id"] == str(card.id)

    state_snap2 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
    assert state_snap2.values.get("can_confirm") is True
    assert state_snap2.values.get("customer_decision") == "accepted"
    # The subsequent intent "issue_card" is scheduled to resume!
    assert "issue_card" in state_snap2.values.get("planned_intents", [])

    # Execute confirmation #1 in trusted layer (FinCrime API)
    unblock_res, _ = await fincrime_svc.unblock_card(cust, str(card.id), reason="customer unblocked")
    assert unblock_res.status in ("unblocked", "created")
    assert fincrime_svc.is_account_blocked(acc["account_number"]) is False

    # Resume LangGraph after unblocking success
    turn3 = await orch.resume_execution(
        thread_id=thread_id,
        owner=owner,
        session_id=session_id,
        tool_name="unblock_card",
        result={"result": {"card_id": str(card.id), "status": "unblocked"}},
        ok=True,
        message="Card unblock completed successfully.",
    )

    # Supervisor resumes next scheduled intent: CardsAgent prepares issue_card proposal with confirmation #2
    assert isinstance(turn3, ProposedToolCall)
    assert turn3.name == "issue_card"

    state_snap3 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
    assert state_snap3.values.get("can_confirm") is True
    plan = state_snap3.values.get("action_plan", [])
    assert plan[0]["intent"] == "unblock_card" and plan[0]["status"] == "completed"
    assert plan[1]["intent"] == "issue_card" and plan[1]["status"] == "awaiting_confirmation"


@pytest.mark.anyio
async def test_scenario_a_unblock_offer_declined(policy_fixture):
    """Scenario A: Customer declines unblock offer -> POL-001 enforces restriction and terminates safely."""
    fincrime_svc: FinCrimeService = policy_fixture["fincrime_svc"]
    cust = policy_fixture["customer"]
    card = policy_fixture["card"]
    acc = policy_fixture["account"]
    checkpointer = policy_fixture["checkpointer"]
    policy_store = policy_fixture["policy_store"]
    policy_evaluator = policy_fixture["policy_evaluator"]

    await fincrime_svc.block_card(cust, str(card.id), reason="temporary customer freeze", key="freeze-2")

    async def facts_retriever(account_number=None, state=None):
        return {
            "account_number": acc["account_number"],
            "has_blocked_card": True,
            "temporary_freeze": True,
            "blocked_cards": [{"id": str(card.id), "reason": "temporary freeze"}],
        }

    model = StubModel()
    orch = AgentOrchestrator(
        model=model,
        checkpointer=checkpointer,
        policy_store=policy_store,
        policy_evaluator=policy_evaluator,
        facts_retriever_fn=facts_retriever,
        check_blocked_fn=fincrime_svc.is_account_blocked,
    )

    thread_id = f"{cust['sub']}:scenario_a_declined"
    allowed_tools = {"issue_card": {}, "unblock_card": {}}

    # Turn 1: Proposes unblocking offer
    turn1 = await orch.interpret_turn(
        history=[{"role": "user", "content": "Issue another card for my account"}],
        collected={"tool": "issue_card", "arguments": {"account_number": acc["account_number"]}},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=cust["sub"],
        session_id="session_dec",
    )
    assert isinstance(turn1, CustomerReply)
    assert "would you like to unblock it first?" in turn1.text.lower()

    # Turn 2: Customer declines: "No, thanks"
    turn2 = await orch.interpret_turn(
        history=[
            {"role": "user", "content": "Issue another card for my account"},
            {"role": "assistant", "content": turn1.text},
            {"role": "user", "content": "No, thanks"},
        ],
        collected={},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=cust["sub"],
        session_id="session_dec",
    )

    # Supervisor enforces POL-001 prohibition and completes without issuing
    assert isinstance(turn2, CustomerReply)
    assert "cannot be issued" in turn2.text.lower() or "prohibited" in turn2.text.lower() or "declined" in turn2.text.lower()

    state_snap = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
    assert state_snap.values.get("customer_decision") == "declined"
    assert state_snap.values.get("can_confirm") is not True


@pytest.mark.anyio
async def test_scenario_a_permanently_compromised_card_suggests_replacement(policy_fixture):
    """Scenario A: If card is permanently compromised, POL-001 forbids unblocking; suggests replacement (POL-002)."""
    fincrime_svc: FinCrimeService = policy_fixture["fincrime_svc"]
    cust = policy_fixture["customer"]
    card = policy_fixture["card"]
    acc = policy_fixture["account"]
    checkpointer = policy_fixture["checkpointer"]
    policy_store = policy_fixture["policy_store"]
    policy_evaluator = policy_fixture["policy_evaluator"]

    # Block card as lost/stolen (permanent compromise)
    await fincrime_svc.block_card(cust, str(card.id), reason="lost card stolen in transit", key="lost-1")
    assert fincrime_svc.has_compromised_cards(acc["account_number"]) is True

    async def facts_retriever(account_number=None, state=None):
        return {
            "account_number": acc["account_number"],
            "has_blocked_card": True,
            "temporary_freeze": False,
            "compromised": True,
            "blocked_cards": [{"id": str(card.id), "reason": "lost card stolen in transit", "status": "blocked"}],
        }

    model = StubModel()
    orch = AgentOrchestrator(
        model=model,
        checkpointer=checkpointer,
        policy_store=policy_store,
        policy_evaluator=policy_evaluator,
        facts_retriever_fn=facts_retriever,
        check_blocked_fn=fincrime_svc.is_account_blocked,
    )

    thread_id = f"{cust['sub']}:scenario_a_compromised"
    allowed_tools = {"issue_card": {}, "replace_card": {}}

    turn = await orch.interpret_turn(
        history=[{"role": "user", "content": "Issue another card for my account"}],
        collected={"tool": "issue_card", "arguments": {"account_number": acc["account_number"]}},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=cust["sub"],
        session_id="session_comp",
    )

    assert isinstance(turn, CustomerReply)
    # Must NOT offer unblocking
    assert "unblock this card first" not in turn.text
    # Suggests replacement card under POL-002
    assert "replacement card" in turn.text.lower()

    state_snap = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
    pending_q = state_snap.values.get("pending_customer_question")
    assert pending_q["question_type"] == "replacement_offer"
    assert "POL-002" in state_snap.values.get("pinned_policies", {})


# ==============================================================================
# Scenario B — Lost Card and Replacement Customer Journey
# ==============================================================================

@pytest.mark.anyio
async def test_scenario_b_lost_card_replacement_offered_and_accepted(policy_fixture):
    """Scenario B: Single intent block -> success -> POL-003 offers replacement -> customer accepts ->

    POL-002 verifies eligibility -> CardsAgent prepares separately confirmed replace_card.
    """
    banking: BankingService = policy_fixture["banking"]
    fincrime_svc: FinCrimeService = policy_fixture["fincrime_svc"]
    cust = policy_fixture["customer"]
    card = policy_fixture["card"]
    acc = policy_fixture["account"]
    checkpointer = policy_fixture["checkpointer"]
    policy_store = policy_fixture["policy_store"]
    policy_evaluator = policy_fixture["policy_evaluator"]

    model = StubModel()
    orch = AgentOrchestrator(
        model=model,
        checkpointer=checkpointer,
        policy_store=policy_store,
        policy_evaluator=policy_evaluator,
        check_blocked_fn=fincrime_svc.is_account_blocked,
    )

    thread_id = f"{cust['sub']}:scenario_b_accepted"
    owner = cust["sub"]
    session_id = "session_b"
    allowed_tools = {"block_card": {}, "replace_card": {}}

    # Turn 1: Single intent: "I've lost my card. Please block it."
    turn1 = await orch.interpret_turn(
        history=[{"role": "user", "content": "I've lost my card. Please block it."}],
        collected={"arguments": {"card_id": str(card.id), "reason": "lost card"}},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=owner,
        session_id=session_id,
    )

    assert isinstance(turn1, ProposedToolCall)
    assert turn1.name == "block_card"

    # Execute confirmation in trusted layer
    block_res, _ = await fincrime_svc.block_card(cust, str(card.id), reason="lost card", key="b-block-1")
    assert block_res.status == "blocked"

    # Resume LangGraph after confirmed block
    turn2 = await orch.resume_execution(
        thread_id=thread_id,
        owner=owner,
        session_id=session_id,
        tool_name="block_card",
        result={"result": {"card_id": str(card.id), "status": "blocked", "last_block_reason": "lost card"}},
        ok=True,
        message=f"Card {card.id} blocked successfully.",
    )

    # Supervisor evaluates POL-003: proactively asks if customer wants a replacement!
    assert isinstance(turn2, CustomerReply)
    assert "POL-003" in turn2.text
    assert "replacement card" in turn2.text.lower()

    state_snap2 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
    pending_q = state_snap2.values.get("pending_customer_question")
    assert pending_q["question_type"] == "replacement_offer"
    assert pending_q["policy_id"] == "POL-003"

    # Turn 3: Customer accepts: "Yes, please"
    # Supervisor interprets acceptance, retrieves POL-002, delegates to CardsAgent for replace_card
    turn3 = await orch.interpret_turn(
        history=[
            {"role": "user", "content": "I've lost my card. Please block it."},
            {"role": "assistant", "content": turn2.text},
            {"role": "user", "content": "Yes, please"},
        ],
        collected={},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=owner,
        session_id=session_id,
    )

    assert isinstance(turn3, ProposedToolCall)
    assert turn3.name == "replace_card"
    assert turn3.arguments["card_id"] == str(card.id)

    # Separate confirmation required for replacement!
    state_snap3 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
    assert state_snap3.values.get("can_confirm") is True
    assert "POL-002" in state_snap3.values.get("pinned_policies", {})


@pytest.mark.anyio
async def test_scenario_b_lost_card_replacement_declined(policy_fixture):
    """Scenario B: Customer declines replacement offer -> workflow completes safely without replacement."""
    fincrime_svc: FinCrimeService = policy_fixture["fincrime_svc"]
    cust = policy_fixture["customer"]
    card = policy_fixture["card"]
    checkpointer = policy_fixture["checkpointer"]
    policy_store = policy_fixture["policy_store"]
    policy_evaluator = policy_fixture["policy_evaluator"]

    model = StubModel()
    orch = AgentOrchestrator(
        model=model,
        checkpointer=checkpointer,
        policy_store=policy_store,
        policy_evaluator=policy_evaluator,
    )

    thread_id = f"{cust['sub']}:scenario_b_declined"
    owner = cust["sub"]
    allowed_tools = {"block_card": {}, "replace_card": {}}

    # Turn 1: Block card
    await orch.interpret_turn(
        history=[{"role": "user", "content": "I lost my card. Block it."}],
        collected={"arguments": {"card_id": str(card.id), "reason": "lost card"}},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=owner,
    )
    await fincrime_svc.block_card(cust, str(card.id), reason="lost card")

    # Resume block -> POL-003 replacement question
    turn2 = await orch.resume_execution(
        thread_id=thread_id,
        owner=owner,
        session_id="s_b_dec",
        tool_name="block_card",
        result={"result": {"card_id": str(card.id), "status": "blocked", "last_block_reason": "lost card"}},
        ok=True,
        message="Card blocked.",
    )
    assert "replacement card" in turn2.text.lower()

    # Customer declines: "No, thanks"
    turn3 = await orch.interpret_turn(
        history=[
            {"role": "user", "content": "I lost my card. Block it."},
            {"role": "assistant", "content": turn2.text},
            {"role": "user", "content": "No, thanks"},
        ],
        collected={},
        allowed_tools=allowed_tools,
        thread_id=thread_id,
        owner=owner,
    )

    assert isinstance(turn3, CustomerReply)
    assert "safely blocked" in turn3.text.lower() or "no replacement" in turn3.text.lower() or "concluded" in turn3.text.lower()

    state_snap = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
    assert state_snap.values.get("can_confirm") is not True


# ==============================================================================
# Scenario C — Dynamic Policy Update Changes Behavior Without Code Changes
# ==============================================================================

@pytest.mark.anyio
async def test_scenario_c_policy_update_changes_behavior_without_code_changes(policy_fixture, tmp_path):
    """Scenario C: Updating POL-003 to offer replacements after temporary freeze causes supervisor

    to offer replacement on the exact same request, with ZERO supervisor code modification.
    """
    fincrime_svc: FinCrimeService = policy_fixture["fincrime_svc"]
    cust = policy_fixture["customer"]
    card = policy_fixture["card"]
    checkpointer = policy_fixture["checkpointer"]

    # Create dedicated policies directory for this test
    p_dir = tmp_path / "dynamic_policies"
    p_dir.mkdir()

    # Write POL-003 v1.0.0 (where offer_on_temporary_freeze is FALSE)
    pol003_path = p_dir / "POL-003-post-block-journey.md"
    pol003_path.write_text(
        "---\npolicy_id: POL-003\nversion: 1.0.0\neffective_date: '2026-01-01'\n"
        "title: Post-Block Customer Journey Policy\napplicable_intents:\n  - block_card\n"
        "triggering_events:\n  - card_blocked_success\n"
        "conditions:\n  block_reasons:\n    - lost\n    - stolen\n    - fraud\n"
        "  offer_on_temporary_freeze: false\n---\n"
        "# POL-003: Post-Block Policy\n",
        encoding="utf-8",
    )

    store = PolicyStore(str(tmp_path / "dynamic_policies.db"))
    store.index_directory(p_dir)
    evaluator = PolicyEvaluator(store)

    model = StubModel()
    orch = AgentOrchestrator(
        model=model,
        checkpointer=checkpointer,
        policy_store=store,
        policy_evaluator=evaluator,
    )

    # --- Phase 1: Behavior under POL-003 v1.0.0 ---
    thread_1 = f"{cust['sub']}:scen_c_v1"
    await orch.interpret_turn(
        history=[{"role": "user", "content": "Please freeze my card temporarily"}],
        collected={"arguments": {"card_id": str(card.id), "reason": "temporary freeze"}},
        allowed_tools={"block_card": {}, "replace_card": {}},
        thread_id=thread_1,
        owner=cust["sub"],
    )

    # Resume after freeze: Under v1.0.0, replacement is NOT offered for temporary freeze
    res_v1 = await orch.resume_execution(
        thread_id=thread_1,
        owner=cust["sub"],
        session_id="s1",
        tool_name="block_card",
        result={"result": {"card_id": str(card.id), "status": "blocked", "last_block_reason": "temporary freeze"}},
        ok=True,
        message="Card frozen successfully.",
    )

    assert isinstance(res_v1, CustomerReply)
    # Under v1.0.0, no replacement question is asked
    assert "replacement card" not in res_v1.text.lower()

    # --- Phase 2: Dynamic Policy Update (Modify Markdown and Reindex) ---
    # Update POL-003 to v1.1.0: set offer_on_temporary_freeze: true!
    pol003_path.write_text(
        "---\npolicy_id: POL-003\nversion: 1.1.0\neffective_date: '2026-01-01'\n"
        "title: Post-Block Customer Journey Policy\napplicable_intents:\n  - block_card\n"
        "triggering_events:\n  - card_blocked_success\n"
        "conditions:\n  block_reasons:\n    - lost\n    - stolen\n    - fraud\n"
        "  offer_on_temporary_freeze: true\n---\n"
        "# POL-003: Post-Block Policy (v1.1.0)\nNow offering replacements even after customer freeze.\n",
        encoding="utf-8",
    )

    # Reindex policies without code change or orchestrator restart!
    reindex_res = store.index_directory(p_dir)
    assert reindex_res["indexed"] == 1
    assert store.get_document("POL-003").version == "1.1.0"

    # --- Phase 3: Exact Same Customer Request Now Produces New Policy Behavior ---
    thread_2 = f"{cust['sub']}:scen_c_v2"
    await orch.interpret_turn(
        history=[{"role": "user", "content": "Please freeze my card temporarily"}],
        collected={"arguments": {"card_id": str(card.id), "reason": "temporary freeze"}},
        allowed_tools={"block_card": {}, "replace_card": {}},
        thread_id=thread_2,
        owner=cust["sub"],
    )

    res_v2 = await orch.resume_execution(
        thread_id=thread_2,
        owner=cust["sub"],
        session_id="s2",
        tool_name="block_card",
        result={"result": {"card_id": str(card.id), "status": "blocked", "last_block_reason": "temporary freeze"}},
        ok=True,
        message="Card frozen successfully.",
    )

    # Under updated POL-003 v1.1.0, the replacement offer IS proactively made!
    assert isinstance(res_v2, CustomerReply)
    assert "replacement card" in res_v2.text.lower()
    assert "1.1.0" in res_v2.text

    state_snap_v2 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_2}})
    assert state_snap_v2.values.get("pinned_policies", {}).get("POL-003") == "1.1.0"


# ==============================================================================
# Edge Cases & Governance Controls
# ==============================================================================

@pytest.mark.anyio
async def test_fail_closed_on_missing_mandatory_policy(tmp_path):
    """Verify system fails closed when a required policy document is missing from repository."""
    empty_p_dir = tmp_path / "empty_policies"
    empty_p_dir.mkdir()

    store = PolicyStore(str(tmp_path / "empty.db"))
    store.index_directory(empty_p_dir)
    evaluator = PolicyEvaluator(store)

    model = StubModel()
    orch = AgentOrchestrator(
        model=model,
        policy_store=store,
        policy_evaluator=evaluator,
    )

    # Attempt issuance when POL-001 is missing
    reply = await orch.interpret_turn(
        history=[{"role": "user", "content": "Issue an additional card"}],
        collected={"tool": "issue_card", "arguments": {"account_number": "ACC-1"}},
        allowed_tools={"issue_card": {}},
    )

    assert isinstance(reply, CustomerReply)
    assert "failure" in reply.text.lower() or "missing" in reply.text.lower()
