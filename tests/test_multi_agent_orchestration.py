"""Comprehensive test suite for LangGraph multi-agent orchestration and specialist handoffs."""
import asyncio
import pytest
from unittest.mock import AsyncMock

from card_chat.agent import CardServicingAgent, FinCrimeAgent, AgentOrchestrator
from card_chat.orchestration.contracts import AgentHandoff, HandoffResult
from card_chat.orchestration.supervisor import SupervisorAgent
from card_chat.orchestration.specialists import CardsSpecialistNode, FinCrimeSpecialistNode
from card_chat.orchestration.graph import create_banking_workflow
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.provider import StubModel


def test_minimal_context_handoff_contract_structure():
    """Verify that agent handoff enforces minimal-context schema and does not pass raw chat."""
    handoff = AgentHandoff(
        origin_agent="supervisor",
        target_agent="fincrime_agent",
        intent="block_card",
        relevant_context={"card_id": "selected_input_abcdefghijklmnopqrstuvwx"},
        session_id="test_session_123",
        workflow_id="wf_001",
        constraints=["require_review"],
    )

    assert handoff.target_agent == "fincrime_agent"
    assert "messages" not in handoff.relevant_context
    assert handoff.relevant_context["card_id"].startswith("selected_input_")

    result = HandoffResult(
        source_agent="fincrime_agent",
        status="requires_confirmation",
        message="Please review: Block Card.",
        tool_proposal={"name": "block_card", "arguments": {"card_id": handoff.relevant_context["card_id"]}},
    )
    assert result.status == "requires_confirmation"
    assert result.tool_proposal["name"] == "block_card"


def test_supervisor_routes_to_cards_specialist():
    """Verify Supervisor delegates account and card servicing intents to CardsAgent."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)

        state = {
            "messages": [{"role": "user", "content": "I would like to view my accounts"}],
            "allowed_tools": {"view_accounts": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }

        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "cards_agent"
        assert result["active_agent"] == "cards_agent"
        assert result["current_handoff"].intent == "view_accounts"

    asyncio.run(scenario())


def test_supervisor_routes_to_fincrime_specialist():
    """Verify Supervisor delegates fraud, freeze, and unblock intents to FinCrimeAgent."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)

        state = {
            "messages": [{"role": "user", "content": "Please block my stolen card immediately"}],
            "allowed_tools": {"block_card": {}, "unblock_card": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }

        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "fincrime_agent"
        assert result["active_agent"] == "fincrime_agent"
        assert result["current_handoff"].intent == "block_card"

    asyncio.run(scenario())


def test_compound_request_planning_and_resumption():
    """Verify that Supervisor detects compound intents and resumes next agent upon specialist success."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)

        # Initial compound turn: "Unblock my card and issue a new card"
        state = {
            "messages": [{"role": "user", "content": "Please unblock my card and issue a new card"}],
            "allowed_tools": {"unblock_card": {}, "issue_card": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }

        first_step = await supervisor.plan_or_route(state)
        assert first_step["next_step"] == "fincrime_agent"
        assert "issue_card" in first_step["planned_intents"]

        # Simulate FinCrimeAgent reporting success back to Supervisor
        fincrime_success = HandoffResult(
            source_agent="fincrime_agent",
            status="success",
            message="Card unblock completed successfully.",
        )

        state["handoff_history"] = [fincrime_success]
        state["planned_intents"] = first_step["planned_intents"]
        state["iteration_count"] = first_step["iteration_count"]

        # Resumption turn: Supervisor receives FinCrime success and delegates to CardsAgent for issuance
        second_step = await supervisor.plan_or_route(state)
        assert second_step["next_step"] == "cards_agent"
        assert second_step["current_handoff"].intent == "issue_card"
        assert second_step["current_handoff"].relevant_context["previous_result"] == "Card unblock completed successfully."

    asyncio.run(scenario())


def test_bounded_reasoning_loop():
    """Verify supervisor bounds reasoning loops and terminates gracefully when max iterations exceeded."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)

        state = {
            "messages": [{"role": "user", "content": "loop test"}],
            "allowed_tools": {},
            "iteration_count": 6,
            "handoff_history": [],
        }

        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "respond"
        assert "safety limit" in result["final_response"]

    asyncio.run(scenario())


def test_cross_agent_pre_issuance_security_rule():
    """Verify that if an account has a blocked card, new card issuance is deterministically rejected."""
    async def scenario():
        model = StubModel()

        async def mock_check_blocked(account_num):
            return account_num == "ACC-BLOCKED-123"

        orchestrator = AgentOrchestrator(
            model=model,
            check_blocked_fn=mock_check_blocked,
        )

        # Directly mock cards_agent to propose issue_card for the blocked account
        orchestrator.cards_agent.interpret_turn = AsyncMock(
            return_value=ProposedToolCall("issue_card", {"account_number": "ACC-BLOCKED-123"})
        )

        reply = await orchestrator.interpret_turn(
            history=[{"role": "user", "content": "Issue a new card for account ACC-BLOCKED-123"}],
            collected={"tool": "issue_card", "arguments": {"account_number": "ACC-BLOCKED-123"}},
            allowed_tools={"issue_card": {}},
        )

        assert isinstance(reply, CustomerReply)
        assert "Cannot issue a new card" in reply.text
        assert "blocked card must be unblocked" in reply.text

    asyncio.run(scenario())


def test_langgraph_workflow_end_to_end_turn():
    """Verify compiled LangGraph StateGraph executes end-to-end and pauses at human confirmation."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)
        cards_agent = CardServicingAgent(model)
        fincrime_agent = FinCrimeAgent(model)

        workflow = create_banking_workflow(supervisor, cards_agent, fincrime_agent)

        # Block card request
        initial_state = {
            "messages": [{"role": "user", "content": "Please block my card"}],
            "verified_draft": {"tool": "block_card", "arguments": {"card_id": "selected_input_abcdefghijklmnopqrstuvwx"}},
            "allowed_tools": {"block_card": {"input_schema": {"properties": {"card_id": {}}}}},
            "handoff_history": [],
            "iteration_count": 0,
        }

        final_state = await workflow.ainvoke(initial_state, config={"configurable": {"thread_id": "test_thread_1"}})
        assert final_state.get("can_confirm") is True
        assert final_state.get("proposed_tool")["name"] == "block_card"

    asyncio.run(scenario())


def test_durable_sqlite_checkpoint_resumption(tmp_path):
    """Verify that multi-agent workflow state persists in SQLite and can be resumed."""
    from card_chat.orchestration.persistence import DurableSqliteSaver

    async def scenario():
        db_path = str(tmp_path / "checkpoints.db")
        saver1 = DurableSqliteSaver(db_path)
        model = StubModel()
        supervisor = SupervisorAgent(model)
        cards_agent = CardServicingAgent(model)
        fincrime_agent = FinCrimeAgent(model)

        workflow1 = create_banking_workflow(supervisor, cards_agent, fincrime_agent, checkpointer=saver1)

        thread_id = "customer_alex:session_42"
        initial_state = {
            "owner": "customer_alex",
            "session_id": "session_42",
            "messages": [{"role": "user", "content": "View my accounts"}],
            "allowed_tools": {"view_accounts": {}},
            "handoff_history": [],
            "iteration_count": 0,
        }

        # First run saves checkpoint
        res1 = await workflow1.ainvoke(initial_state, config={"configurable": {"thread_id": thread_id}})
        assert res1 is not None
        saver1.close()

        # Simulate service restart: create fresh saver pointing to same SQLite DB
        saver2 = DurableSqliteSaver(db_path)
        workflow2 = create_banking_workflow(supervisor, cards_agent, fincrime_agent, checkpointer=saver2)

        tuple_res = await saver2.aget_tuple({"configurable": {"thread_id": thread_id}})
        assert tuple_res is not None
        assert tuple_res.checkpoint["channel_values"]["owner"] == "customer_alex"
        saver2.close()

    asyncio.run(scenario())


def test_fail_closed_outbound_pii_check():
    """Verify fail-closed outbound PII validation prevents sensitive leakage."""
    from card_chat.pii.gateway import PIIGateway, PIILeakageError
    from card_chat.pii.vault import InMemoryTokenVault

    gateway = PIIGateway(vault=InMemoryTokenVault())

    # Raw account number without tokenization should trigger fail-closed error
    with pytest.raises(PIILeakageError):
        gateway.verify_outbound("The user account is 0012345678 and CVV is 123")

    # Raw Australian TFN should trigger fail-closed error
    with pytest.raises(PIILeakageError):
        gateway.verify_outbound("Here is my TFN 123456782")

    # Tokenized reference string should pass safely
    gateway.verify_outbound("Please view account selected_account_abcdefghijklmnopqrstuvwx")


def test_end_to_end_tokenization_and_resolution():
    """Verify customer account input is sanitized to a token and resolved within trusted boundary."""
    from card_chat.pii.gateway import PIIGateway
    from card_chat.pii.vault import InMemoryTokenVault

    vault = InMemoryTokenVault()
    gateway = PIIGateway(vault=vault)

    # 1. Inbound sanitization
    res = gateway.sanitize_input(
        "Please issue a card for account 0012345678",
        owner="cust-1",
        session_id="sess-1",
    )
    assert "0012345678" not in res.text
    assert len(res.tokens_created) == 1
    token, raw_val = next(iter(res.tokens_created.items()))
    assert raw_val == "0012345678"
    assert token.startswith("selected_account_")

    # 2. Outbound verification: sanitized text passes
    gateway.verify_outbound(res.text)

    # 3. Trusted execution resolution: resolves token back to raw account number
    resolved = gateway.resolve_tokens({"account_number": token}, owner="cust-1", session_id="sess-1")
    assert resolved["account_number"] == "0012345678"

    # 4. Session isolation check: another customer cannot resolve the token
    with pytest.raises(ValueError, match="could not be resolved or session isolation check failed"):
        gateway.resolve_tokens({"account_number": token}, owner="outsider", session_id="sess-1")


def test_compromised_card_cannot_be_unblocked(tmp_path):
    """Verify that a permanently compromised card (lost/stolen/fraud) cannot be unblocked."""
    from fincrime_servicing.service import FinCrimeService
    from fincrime_servicing.persistence import SQLiteFinCrimeStore
    from fastapi import HTTPException

    store = SQLiteFinCrimeStore(str(tmp_path / "fincrime.db"))
    store.initialize()
    service = FinCrimeService(store=store)

    cust = {"sub": "cust-99", "username": "alice"}
    card_id = "card-lost-001"
    account_num = "0012345678"

    # Block card with reason 'Lost by customer'
    async def scenario():
        res, _ = await service.block_card(cust, card_id, reason="Lost by customer in public park", account_number=account_num)
        assert res.status == "blocked"

        # Attempt to unblock the lost/compromised card
        with pytest.raises(HTTPException) as exc_info:
            await service.unblock_card(cust, card_id, reason="Found it", account_number=account_num)

        assert exc_info.value.status_code == 400
        assert "Permanently blocked or compromised cards cannot be unblocked" in exc_info.value.detail

    asyncio.run(scenario())


def test_remote_fincrime_agent_explicit_failure_no_silent_fallback():
    """Verify that RemoteFinCrimeAgent fails explicitly and does not silently fall back when allow_fallback=False."""
    from card_chat.fincrime_agent import RemoteFinCrimeAgent

    class BrokenAgent(RemoteFinCrimeAgent):
        async def interpret_turn(self, history, collected, allowed_tools):
            return await super().interpret_turn(history, collected, allowed_tools)

    agent = BrokenAgent("http://non-existent-service:9999", fallback_agent=None, allow_fallback=False)

    async def scenario():
        reply = await agent.interpret_turn([], {}, {})
        assert isinstance(reply, CustomerReply)
        assert "trouble connecting" in reply.text.lower()

    asyncio.run(scenario())

