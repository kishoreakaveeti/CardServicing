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



def test_remote_fincrime_agent_sends_authorization_headers():
    """Verify that RemoteFinCrimeAgent includes service authorization headers in outgoing requests."""
    from card_chat.fincrime_agent import RemoteFinCrimeAgent
    from unittest.mock import patch

    class MockResponse:
        status_code = 200
        def json(self):
            return {"kind": "reply", "text": "FinCrime response"}

    async def mock_headers():
        return {"Authorization": "Bearer test-service-token", "X-Correlation-ID": "test-trace-id"}

    async def scenario():
        agent = RemoteFinCrimeAgent("http://fincrime-service:8022", headers_fn=mock_headers)
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = MockResponse()
            reply = await agent.interpret_turn([], {}, {})
            assert mock_post.called
            call_kwargs = mock_post.call_args.kwargs
            assert call_kwargs.get("headers") == {
                "Authorization": "Bearer test-service-token",
                "X-Correlation-ID": "test-trace-id",
            }
            assert isinstance(reply, CustomerReply)
            assert reply.text == "FinCrime response"

    asyncio.run(scenario())


def test_supervisor_routes_issue_new_card_to_cards_agent():
    """Verify that 'Issue a new card' routes to Cards agent with issue_card intent."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)
        state = {
            "messages": [{"role": "user", "content": "Issue a new card"}],
            "allowed_tools": {"issue_card": {}, "view_accounts": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "cards_agent"
        assert result["active_agent"] == "cards_agent"
        assert result["current_handoff"].intent == "issue_card"
        assert result["current_handoff"].target_agent == "cards_agent"

    asyncio.run(scenario())


def test_supervisor_routes_show_my_accounts_to_cards_agent():
    """Verify that 'Show my accounts' routes to Cards agent with view_accounts intent."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)
        state = {
            "messages": [{"role": "user", "content": "Show my accounts"}],
            "allowed_tools": {"view_accounts": {}, "block_card": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "cards_agent"
        assert result["active_agent"] == "cards_agent"
        assert result["current_handoff"].intent == "view_accounts"
        assert result["current_handoff"].target_agent == "cards_agent"

    asyncio.run(scenario())


def test_supervisor_routes_lost_card_to_fincrime_agent():
    """Verify that 'I've lost my card' routes to FinCrime agent with block_card intent."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)
        state = {
            "messages": [{"role": "user", "content": "I've lost my card"}],
            "allowed_tools": {"block_card": {}, "view_accounts": {}, "view_cards": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "fincrime_agent"
        assert result["active_agent"] == "fincrime_agent"
        assert result["current_handoff"].intent == "block_card"
        assert result["current_handoff"].target_agent == "fincrime_agent"

    asyncio.run(scenario())


def test_supervisor_compound_block_and_issue_replacement():
    """Verify compound request 'Block my card and issue a replacement' produces ordered intents."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)
        state = {
            "messages": [{"role": "user", "content": "Block my card and issue a replacement"}],
            "allowed_tools": {"block_card": {}, "issue_card": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "fincrime_agent"
        assert result["current_handoff"].intent == "block_card"
        assert result["planned_intents"] == ["issue_card"]

    asyncio.run(scenario())


def test_supervisor_explicit_ui_action_no_llm():
    """Verify explicit UI action selection routes directly without unnecessary LLM invocation."""
    async def scenario():
        mock_messages = AsyncMock()
        mock_client = AsyncMock()
        mock_client.messages = mock_messages
        mock_model = AsyncMock()
        mock_model.client = mock_client
        mock_model.model = "claude-haiku-5-5"

        supervisor = SupervisorAgent(mock_model)
        state = {
            "messages": [{"role": "user", "content": "confirm"}],
            "verified_draft": {"tool": "create_card", "arguments": {"account_number": "ref_123"}},
            "allowed_tools": {"create_card": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "cards_agent"
        assert result["current_handoff"].intent == "create_card"
        assert not mock_messages.create.called

    asyncio.run(scenario())


def test_supervisor_invalid_unauthorized_wrong_domain_intent():
    """Verify that domain ownership is enforced independently of whatever target agent the LLM specifies."""
    async def scenario():
        class MockLLMResponse:
            content = [type("Block", (), {"type": "text", "text": '{"target": "cards_agent", "intents": ["block_card"]}'})()]

        mock_messages = AsyncMock()
        mock_messages.create = AsyncMock(return_value=MockLLMResponse())
        mock_client = AsyncMock()
        mock_client.messages = mock_messages
        mock_model = AsyncMock()
        mock_model.client = mock_client
        mock_model.model = "claude-test-model"

        supervisor = SupervisorAgent(mock_model)
        state = {
            "messages": [{"role": "user", "content": "Freeze my card"}],
            "allowed_tools": {"block_card": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "fincrime_agent"
        assert result["active_agent"] == "fincrime_agent"
        assert result["current_handoff"].target_agent == "fincrime_agent"

    asyncio.run(scenario())


def test_supervisor_ambiguous_request_requests_clarification():
    """Verify ambiguous requests ask for clarification rather than defaulting to view_accounts."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)
        state = {
            "messages": [{"role": "user", "content": "What is the meaning of life?"}],
            "allowed_tools": {"view_accounts": {}, "issue_card": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "respond"
        assert "not sure what you'd like to do" in result["final_response"].lower() or "clarify" in result["final_response"].lower()
        assert "current_handoff" not in result

    asyncio.run(scenario())


def test_supervisor_llm_failure_deterministic_fallback():
    """Verify that when LLM call fails, deterministic fallback accurately routes the user's intent."""
    async def scenario():
        mock_messages = AsyncMock()
        mock_messages.create = AsyncMock(side_effect=RuntimeError("Anthropic API connection timeout"))
        mock_client = AsyncMock()
        mock_client.messages = mock_messages
        mock_model = AsyncMock()
        mock_model.client = mock_client
        mock_model.model = "claude-test-model"

        supervisor = SupervisorAgent(mock_model)
        state = {
            "messages": [{"role": "user", "content": "I've lost my card, please block it"}],
            "allowed_tools": {"block_card": {}, "view_accounts": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result = await supervisor.plan_or_route(state)
        assert result["next_step"] == "fincrime_agent"
        assert result["active_agent"] == "fincrime_agent"
        assert result["current_handoff"].intent == "block_card"

    asyncio.run(scenario())


def test_supervisor_restricted_tool_catalogue():
    """Verify that requests for tools outside a restricted tool catalogue are rejected or safely filtered."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)

        # Single request with restricted catalogue: user asks to block card, but only view_accounts is authorized
        state1 = {
            "messages": [{"role": "user", "content": "Please block my card"}],
            "allowed_tools": {"view_accounts": {}},
            "iteration_count": 0,
            "handoff_history": [],
        }
        result1 = await supervisor.plan_or_route(state1)
        assert result1["next_step"] == "respond"
        assert "not have authorization" in result1["final_response"].lower()

        # Compound request with restricted catalogue: user asks to block card and issue card, but only block_card is authorized
        state2 = {
            "messages": [{"role": "user", "content": "Block my card and issue a replacement"}],
            "allowed_tools": {"block_card": {}},  # issue_card not authorized
            "iteration_count": 0,
            "handoff_history": [],
        }
        result2 = await supervisor.plan_or_route(state2)
        assert result2["next_step"] == "fincrime_agent"
        assert result2["current_handoff"].intent == "block_card"
        assert result2["planned_intents"] == []

    asyncio.run(scenario())


def test_subsequent_lost_card_after_account_creation_routes_to_fincrime():
    """Verify that after creating an account with card, typing 'i lost my card' routes to block_card, not view_cards."""
    async def scenario():
        model = StubModel()
        supervisor = SupervisorAgent(model)
        cards_agent = CardServicingAgent(model)
        fincrime_agent = FinCrimeAgent(model)
        workflow = create_banking_workflow(supervisor, cards_agent, fincrime_agent)

        allowed = {
            "create_account": {"input_schema": {"properties": {"account_type": {}}}},
            "create_card": {},
            "view_accounts": {},
            "view_cards": {},
            "issue_card": {},
            "block_card": {"input_schema": {"properties": {"card_id": {}}}},
        }

        # Turn 1: User creates account with card
        state1 = {
            "messages": [{"role": "user", "content": "create account with card"}],
            "allowed_tools": allowed,
            "verified_draft": {},
            "iteration_count": 0,
            "handoff_history": [],
        }
        res1 = await workflow.ainvoke(state1, config={"configurable": {"thread_id": "turn_1"}})
        assert res1["handoff_history"][-1].source_agent == "cards_agent"
        assert res1["proposed_tool"] is not None
        assert res1["proposed_tool"]["name"] in ("create_account", "create_card")

        # Turn 2: Subsequent request: user types 'i lost my card'
        # Crucial: in multi-turn chat, initial_state resets active_agent and proposed_tool
        state2 = {
            "messages": [
                {"role": "user", "content": "create account with card"},
                {"role": "assistant", "content": "I have created account [acc_ref_1]"},
                {"role": "user", "content": "i lost my card"},
            ],
            "allowed_tools": allowed,
            "verified_draft": {},
            "iteration_count": 0,
            "handoff_history": [],
            "active_agent": None,
            "proposed_tool": None,
        }
        res2 = await workflow.ainvoke(state2, config={"configurable": {"thread_id": "turn_2"}})
        assert res2["handoff_history"][-1].source_agent == "fincrime_agent"
        assert res2["proposed_tool"] is not None
        assert res2["proposed_tool"]["name"] == "block_card"

    asyncio.run(scenario())
