"""Tests for complete, resumable compound multi-agent workflows using LangGraph.

Covers:
1. Successful block -> replacement workflow with two separate human confirmations.
2. Customer rejects first confirmation (blocking cancelled, replacement never runs).
3. Customer rejects second confirmation (card remains blocked, replacement cancelled).
4. FinCrime blocking fails (dependent replacement never runs).
5. Replacement eligibility fails after successful blocking.
6. Duplicate confirmation and idempotent retry.
7. Restart and resume across process boundaries with DurableSqliteSaver.
8. Unauthorized / cross-customer workflow access isolation.
9. Single-intent workflows remain unaffected.
"""
import asyncio
from uuid import uuid4
import pytest

from card_chat.provider import StubModel
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.orchestration.supervisor import SupervisorAgent
from card_chat.orchestration.persistence import DurableSqliteSaver
from card_chat.agent import AgentOrchestrator, CardServicingAgent, FinCrimeAgent
from card_servicing.models import CardReplaceInput
from card_servicing.persistence import SQLiteCardStore
from card_servicing.banking import BankingService, Credentials, AccountCreate


@pytest.fixture
def test_setup(tmp_path):
    """Fixture providing real SQLite card store, banking service, and orchestrator."""
    db_file = str(tmp_path / "test_checkpoints.db")
    cards_db = str(tmp_path / "cards.db")

    checkpointer = DurableSqliteSaver(db_file)
    store = SQLiteCardStore(cards_db)
    store.initialize()
    banking = BankingService(store)

    # Register customer and create initial account with card
    reg = banking.register(Credentials(username="alex", password="synthetic password 123"))
    cust = {"sub": reg["sub"], "username": "alex", "preferred_username": "alex", "accounts": []}
    acc = banking.create_account(cust, AccountCreate(account_type="savings", issue_card=True), key="init_acc_key")
    cust["accounts"] = [acc["account_number"]]
    geo = banking.declare_country(cust, uuid4().hex,
        {'code': 'AU', 'status': 'explicit', 'source': 'customer_declaration'})

    cards = banking.cards(cust["sub"])
    assert len(cards) == 1
    card = cards[0]
    assert card.status == "created"

    model = StubModel()
    supervisor = SupervisorAgent(model)
    cards_agent = CardServicingAgent(model)
    fincrime_agent = FinCrimeAgent(model)

    orchestrator = AgentOrchestrator(
        model=model,
        checkpointer=checkpointer,
        check_blocked_fn=store.has_blocked_cards_for_account,
    )
    return {
        "store": store,
        "banking": banking,
        "model": model,
        "orchestrator": orchestrator,
        "checkpointer": checkpointer,
        "db_file": db_file,
        "customer": cust,
        "card": card,
        "geographic_context": geo,
    }


def test_successful_block_and_replacement_workflow(test_setup):
    """1. Successful block -> replacement workflow with two separate human confirmations."""
    async def scenario():
        orch: AgentOrchestrator = test_setup["orchestrator"]
        banking: BankingService = test_setup["banking"]
        cust = test_setup["customer"]
        card = test_setup["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:workflow_compound_1"
        owner = cust["sub"]
        session_id = "session_1"
        allowed_tools = {"block_card": {}, "replace_card": {}, "issue_card": {}}

        # Initial customer message: compound request
        history = [{"role": "user", "content": "I'm in Australia. I've lost my card. Block it and issue a replacement."}]
        collected = {"arguments": {"card_id": card_id, "reason": "lost card"}}

        # Turn 1: Supervisor plans compound workflow, FinCrime prepares block_card, pauses for confirmation #1
        turn1 = await orch.interpret_turn(
            history=history,
            collected=collected,
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
        )

        assert isinstance(turn1, ProposedToolCall)
        assert turn1.name == "block_card"
        assert turn1.arguments["card_id"] == card_id

        # Verify LangGraph paused at human_confirmation node
        snap1 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
        assert len(snap1.next) > 0
        plan = snap1.values.get("action_plan", [])
        assert len(plan) == 2
        assert plan[0]["intent"] == "block_card"
        assert plan[0]["status"] == "awaiting_confirmation"
        assert plan[1]["intent"] == "replace_card"
        assert plan[1]["status"] == "pending"

        # Execute confirmation #1 in trusted execution layer (FinCrime API)
        assert banking.store.get_card(card_id).status == "created"
        block_result = banking.block_card(cust, card_id, reason="lost card")
        assert block_result["status"] == "blocked"
        assert banking.store.get_card(card_id).status == "blocked"

        # Resume LangGraph with actual block result
        turn2 = await orch.resume_execution(
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
            tool_name="block_card",
            result={"result": block_result},
            ok=True,
            message=f"Card {card_id} blocked successfully.",
        )

        # Step 2: Cards Specialist verifies replacement eligibility (card is blocked), prepares replace_card proposal
        assert isinstance(turn2, ProposedToolCall)
        assert turn2.name == "replace_card"
        assert turn2.arguments["card_id"] == card_id

        # Verify LangGraph paused again for separate confirmation #2
        snap2 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
        assert len(snap2.next) > 0
        plan2 = snap2.values.get("action_plan", [])
        assert plan2[0]["status"] == "completed"
        assert plan2[1]["intent"] == "replace_card"
        assert plan2[1]["status"] == "awaiting_confirmation"

        # Execute confirmation #2 in trusted execution layer (Cards API)
        replace_result = banking.replace_card(
            cust, card_id, key="idempotency_key_1", reason="lost card replacement",
            geographic_context=test_setup['geographic_context']
        )
        assert replace_result.outcome == "replacement_created"
        assert str(replace_result.id) != card_id
        assert replace_result.status == "created"

        # Invariant: Original compromised card is NOT unblocked
        assert banking.store.get_card(card_id).status == "blocked"

        # Resume LangGraph with actual replacement result
        turn3 = await orch.resume_execution(
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
            tool_name="replace_card",
            result={"result": replace_result.model_dump()},
            ok=True,
            message="Replacement card issued successfully.",
        )

        # Final turn: Workflow complete
        assert isinstance(turn3, CustomerReply)
        snap3 = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
        assert len(snap3.next) == 0  # Completed
        plan3 = snap3.values.get("action_plan", [])
        assert all(item["status"] == "completed" for item in plan3)

    asyncio.run(scenario())


def test_customer_rejects_first_confirmation(test_setup):
    """2. Customer rejects first confirmation (blocking cancelled, replacement never runs)."""
    async def scenario():
        orch: AgentOrchestrator = test_setup["orchestrator"]
        banking: BankingService = test_setup["banking"]
        cust = test_setup["customer"]
        card = test_setup["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:workflow_cancel_1"
        owner = cust["sub"]
        session_id = "session_1"
        allowed_tools = {"block_card": {}, "replace_card": {}}

        turn1 = await orch.interpret_turn(
            history=[{"role": "user", "content": "I am in Australia. Lost card, block and replace"}],
            collected={"arguments": {"card_id": card_id}},
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
        )
        assert isinstance(turn1, ProposedToolCall)

        # Customer rejects / cancels first confirmation
        cancel_reply = await orch.cancel_execution(thread_id, owner, session_id)
        assert isinstance(cancel_reply, CustomerReply)
        assert "cancelled" in cancel_reply.text.lower()

        # Invariant: Card is NOT blocked, and replacement never executed
        assert banking.store.get_card(card_id).status == "created"
        assert len(banking.cards(cust["sub"])) == 1

        snap = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
        assert len(snap.next) == 0
        plan = snap.values.get("action_plan", [])
        assert plan[0]["status"] == "cancelled"
        assert plan[1]["status"] == "cancelled"

    asyncio.run(scenario())


def test_customer_rejects_second_confirmation(test_setup):
    """3. Customer rejects second confirmation (card remains blocked, replacement cancelled)."""
    async def scenario():
        orch: AgentOrchestrator = test_setup["orchestrator"]
        banking: BankingService = test_setup["banking"]
        cust = test_setup["customer"]
        card = test_setup["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:workflow_cancel_2"
        owner = cust["sub"]
        session_id = "session_1"
        allowed_tools = {"block_card": {}, "replace_card": {}}

        await orch.interpret_turn(
            history=[{"role": "user", "content": "I am in Australia. Lost card, block and replace"}],
            collected={"arguments": {"card_id": card_id}},
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
        )

        # Confirm step 1
        block_result = banking.block_card(cust, card_id, reason="lost card")
        turn2 = await orch.resume_execution(
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
            tool_name="block_card",
            result={"result": block_result},
            ok=True,
            message="Card blocked.",
        )
        assert isinstance(turn2, ProposedToolCall)
        assert turn2.name == "replace_card"

        # Customer rejects Step 2
        cancel_reply = await orch.cancel_execution(thread_id, owner, session_id)
        assert isinstance(cancel_reply, CustomerReply)

        # CRITICAL INVARIANTS:
        # 1. Compromised card remains BLOCKED (no false rollback)
        assert banking.store.get_card(card_id).status == "blocked"
        # 2. No replacement card was issued
        assert len(banking.cards(cust["sub"])) == 1
        # 3. Plan shows step 1 completed, step 2 cancelled
        snap = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
        plan = snap.values.get("action_plan", [])
        assert plan[0]["status"] == "completed"
        assert plan[1]["status"] == "cancelled"

    asyncio.run(scenario())


def test_fincrime_blocking_fails(test_setup):
    """4. FinCrime blocking fails (dependent replacement never runs)."""
    async def scenario():
        orch: AgentOrchestrator = test_setup["orchestrator"]
        banking: BankingService = test_setup["banking"]
        cust = test_setup["customer"]
        card = test_setup["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:workflow_fail_block"
        owner = cust["sub"]
        session_id = "session_1"
        allowed_tools = {"block_card": {}, "replace_card": {}}

        await orch.interpret_turn(
            history=[{"role": "user", "content": "I am in Australia. Lost card, block and replace"}],
            collected={"arguments": {"card_id": card_id}},
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
        )

        # FinCrime API returns error
        resumed = await orch.resume_execution(
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
            tool_name="block_card",
            result={},
            ok=False,
            error="Core banking card blocking service unavailable",
        )
        assert isinstance(resumed, CustomerReply)
        assert "unavailable" in resumed.text.lower() or "failed" in resumed.text.lower()

        # Dependent action (replacement) must NOT run
        snap = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
        assert len(snap.next) == 0
        plan = snap.values.get("action_plan", [])
        assert plan[0]["status"] == "failed"
        assert plan[1]["status"] == "cancelled"
        assert len(banking.cards(cust["sub"])) == 1

    asyncio.run(scenario())


def test_replacement_eligibility_fails_after_successful_blocking(test_setup):
    """5. Replacement eligibility fails after successful blocking."""
    async def scenario():
        orch: AgentOrchestrator = test_setup["orchestrator"]
        banking: BankingService = test_setup["banking"]
        cust = test_setup["customer"]
        card = test_setup["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:workflow_fail_eligibility"
        owner = cust["sub"]
        session_id = "session_1"
        allowed_tools = {"block_card": {}, "replace_card": {}}

        await orch.interpret_turn(
            history=[{"role": "user", "content": "I am in Australia. Lost card, block and replace"}],
            collected={"arguments": {"card_id": card_id}},
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
        )

        # Step 1 succeeds
        block_result = banking.block_card(cust, card_id, reason="lost card")
        turn2 = await orch.resume_execution(
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
            tool_name="block_card",
            result={"result": block_result},
            ok=True,
            message="Card blocked.",
        )
        assert isinstance(turn2, ProposedToolCall)

        # Simulate replacement eligibility check failure in API (e.g. account closed / limit reached)
        failed_resumed = await orch.resume_execution(
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
            tool_name="replace_card",
            result={},
            ok=False,
            error="Account ineligible for card replacement",
        )
        assert isinstance(failed_resumed, CustomerReply)

        # Card remains safely blocked; partial completion handled safely
        assert banking.store.get_card(card_id).status == "blocked"
        snap = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
        plan = snap.values.get("action_plan", [])
        assert plan[0]["status"] == "completed"
        assert plan[1]["status"] == "failed"

    asyncio.run(scenario())


def test_duplicate_confirmation_and_retry(test_setup):
    """6. Duplicate confirmation and idempotent retry."""
    banking: BankingService = test_setup["banking"]
    cust = test_setup["customer"]
    card = test_setup["card"]
    card_id = str(card.id)

    # First block the card
    banking.block_card(cust, card_id, reason="lost")

    # First execution with idempotency key
    key = "idem_key_replace_123"
    res1, replayed1 = banking.replace_card(cust, card_id, key=key, reason="lost", with_replay=True, geographic_context=test_setup['geographic_context'])
    assert res1.outcome == "replacement_created"
    assert replayed1 is False
    new_card_id = res1.id

    # Duplicate submission with same idempotency key
    res2, replayed2 = banking.replace_card(cust, card_id, key=key, reason="lost", with_replay=True, geographic_context=test_setup['geographic_context'])
    assert res2.outcome == "replacement_created"
    assert replayed2 is True
    assert res2.id == new_card_id  # Returns identical existing replacement, never duplicates!


def test_restart_and_resume_across_process(tmp_path):
    """7. Restart and resume across process boundaries with DurableSqliteSaver."""
    db_file = str(tmp_path / "durable_checkpoints.db")
    cards_db = str(tmp_path / "cards_restart.db")
    store = SQLiteCardStore(cards_db)
    store.initialize()
    banking = BankingService(store)

    reg = banking.register(Credentials(username="restart_user", password="password 12345"))
    cust = {"sub": reg["sub"], "username": "restart_user", "preferred_username": "restart_user", "accounts": []}
    acc = banking.create_account(cust, AccountCreate(account_type="savings", issue_card=True), key="acc_key")
    cust["accounts"] = [acc["account_number"]]
    card = banking.cards(cust["sub"])[0]
    card_id = str(card.id)

    thread_id = f"{cust['sub']}:session_restart_test"
    owner = cust["sub"]
    session_id = "session_restart"

    # Process 1: Start workflow, pause at confirmation
    async def process1():
        saver1 = DurableSqliteSaver(db_file)
        model = StubModel()
        orch1 = AgentOrchestrator(
            model=model,
            checkpointer=saver1,
        )
        turn = await orch1.interpret_turn(
            history=[{"role": "user", "content": "I am in Australia. Lost my card, block and replace"}],
            collected={"arguments": {"card_id": card_id}},
            allowed_tools={"block_card": {}, "replace_card": {}},
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
        )
        assert isinstance(turn, ProposedToolCall)
        assert turn.name == "block_card"
        saver1.close()

    asyncio.run(process1())

    # Process 2: Brand new orchestrator and StateGraph pointing to the saved checkpoint database
    async def process2():
        saver2 = DurableSqliteSaver(db_file)
        model = StubModel()
        orch2 = AgentOrchestrator(
            model=model,
            checkpointer=saver2,
        )

        # Resume execution on newly instantiated orchestrator
        resumed = await orch2.resume_execution(
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
            tool_name="block_card",
            result={"result": {"id": card_id, "status": "blocked"}},
            ok=True,
            message="Card blocked.",
        )

        assert isinstance(resumed, ProposedToolCall)
        assert resumed.name == "replace_card"
        assert resumed.arguments["card_id"] == card_id
        saver2.close()

    asyncio.run(process2())


def test_unauthorized_and_cross_session_access(test_setup):
    """8. Unauthorized or cross-session workflow access fails closed."""
    async def scenario():
        orch: AgentOrchestrator = test_setup["orchestrator"]
        cust = test_setup["customer"]
        card = test_setup["card"]

        thread_id = f"{cust['sub']}:secret_workflow"
        owner = cust["sub"]
        session_id = "session_alex"

        # Alex starts a compound workflow
        await orch.interpret_turn(
            history=[{"role": "user", "content": "I am in Australia. Lost my card, block and replace"}],
            collected={"arguments": {"card_id": str(card.id)}},
            allowed_tools={"block_card": {}, "replace_card": {}},
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
        )

        # Malicious attacker "bob" attempts to resume Alex's workflow
        with pytest.raises(PermissionError):
            await orch.resume_execution(
                thread_id=thread_id,
                owner="cust_bob",  # Mismatch!
                session_id="session_bob",
                tool_name="block_card",
                result={"result": {"status": "blocked"}},
                ok=True,
            )

        # Malicious attacker "bob" attempts to cancel Alex's workflow
        with pytest.raises(PermissionError):
            await orch.cancel_execution(
                thread_id=thread_id,
                owner="cust_bob",  # Mismatch!
                session_id="session_bob",
            )

    asyncio.run(scenario())


def test_single_intent_workflow_unaffected(test_setup):
    """9. Single-intent workflows remain unaffected and do not trigger compound chaining."""
    async def scenario():
        orch: AgentOrchestrator = test_setup["orchestrator"]
        cust = test_setup["customer"]

        thread_id = f"{cust['sub']}:single_intent"
        owner = cust["sub"]
        session_id = "session_single"

        # Single intent: View accounts
        test_setup["model"].outputs = [ProposedToolCall("view_accounts", {})]
        turn = await orch.interpret_turn(
            history=[{"role": "user", "content": "View my accounts"}],
            collected={},
            allowed_tools={"view_accounts": {}},
            thread_id=thread_id,
            owner=owner,
            session_id=session_id,
        )
        assert isinstance(turn, ProposedToolCall)
        assert turn.name == "view_accounts"

        snap = await orch.workflow.aget_state({"configurable": {"thread_id": thread_id}})
        plan = snap.values.get("action_plan", [])
        assert len(plan) == 1
        assert plan[0]["intent"] == "view_accounts"

    asyncio.run(scenario())


def test_replace_card_mcp_catalogue_and_schema_validation(test_setup):
    """10. Verify replace_card in MCP catalogue validates OpenAPI CardIssuance response with replacement_created."""
    from card_chat.catalogue import load_catalogue
    from card_chat.mcp_server import Draft202012Validator, FormatChecker

    catalogue = load_catalogue({})
    assert "replace_card" in catalogue
    spec = catalogue["replace_card"]
    assert spec.entry.operation_id == "replace_card"
    assert "reason" in spec.entry.context_fields
    assert "card_id" in spec.entry.trusted_fields

    schema = spec.responses.get(201)
    assert schema is not None

    cust = test_setup["customer"]
    card = test_setup["card"]
    banking = test_setup["banking"]

    # Block card first
    banking.block_card(cust, card.id, reason="lost card")

    # Replace card
    replace_result = banking.replace_card(cust, card.id, key="replace_key_1", reason="lost card", geographic_context=test_setup['geographic_context'])
    assert replace_result.outcome == "replacement_created"

    # Validate response against OpenAPI schema
    data = replace_result.model_dump(mode="json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert validator.is_valid(data), f"Schema validation failed for replace_card response: {list(validator.iter_errors(data))}"

