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
