"""Multi-agent orchestration and specialized agents for card servicing and FinCrime using LangGraph."""
import logging
from uuid import uuid4
from typing import Any

from card_chat.provider import Model
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.orchestration.contracts import HandoffResult
from card_chat.model_context import build_context
from card_chat.orchestration.supervisor import SupervisorAgent
from card_chat.orchestration.graph import create_banking_workflow
from card_chat.policy.ports import PolicyRetriever, PolicyEvaluator

logger = logging.getLogger(__name__)


class CardServicingAgent:
    """Specialized agent handling account lifecycle, card viewing, and new issuance."""
    def __init__(self, model: Model):
        self.model = model
        self.name = "cards_agent"

    async def interpret_turn(self, history: list, collected: dict,
                             allowed_tools: dict) -> CustomerReply | ProposedToolCall:
        history, collected, tools = build_context(history, collected, allowed_tools)
        from card_chat.pii.gateway import get_default_pii_gateway
        gateway = get_default_pii_gateway()
        if gateway:
            gateway.verify_outbound(str(history) + " " + str(collected))
        turn = await self.model.turn(history, collected, tools)
        if isinstance(turn, ProposedToolCall):
            if turn.name not in tools:
                raise ValueError('Model proposed an unavailable tool')
            if not isinstance(turn.arguments, dict):
                raise ValueError('Tool arguments must be an object')
        elif not isinstance(turn, CustomerReply) or not turn.text.strip():
            raise ValueError('Model returned an invalid conversation turn')
        return turn


class FinCrimeAgent:
    """Specialized agent handling financial crime protection, risk checks, and card blocking."""
    def __init__(self, model: Model):
        self.model = model
        self.name = "fincrime_agent"

    async def interpret_turn(self, history: list, collected: dict,
                             allowed_tools: dict) -> CustomerReply | ProposedToolCall:
        history, collected, tools = build_context(history, collected, allowed_tools)
        from card_chat.pii.gateway import get_default_pii_gateway
        gateway = get_default_pii_gateway()
        if gateway:
            gateway.verify_outbound(str(history) + " " + str(collected))
        turn = await self.model.turn(history, collected, tools)
        if isinstance(turn, ProposedToolCall):
            if turn.name not in tools:
                raise ValueError('FinCrime agent proposed an unavailable tool')
            if not isinstance(turn.arguments, dict):
                raise ValueError('Tool arguments must be an object')
        elif not isinstance(turn, CustomerReply) or not turn.text.strip():
            raise ValueError('FinCrime agent returned an invalid conversation turn')
        return turn


class AgentOrchestrator:
    """LangGraph Supervisor Orchestrator replacing keyword routing with multi-agent reasoning."""

    def __init__(self, model: Model, cards_agent: CardServicingAgent | None = None,
                 fincrime_agent: FinCrimeAgent | None = None,
                 check_blocked_fn=None,
                 agent_tools_policy: dict | None = None,
                 checkpointer=None,
                 policy_retriever: PolicyRetriever | None = None,
                 policy_evaluator: PolicyEvaluator | None = None,
                 facts_retriever_fn: Any | None = None):
        self.model = model
        self.cards_agent = cards_agent or CardServicingAgent(model)
        self.fincrime_agent = fincrime_agent or FinCrimeAgent(model)
        self.check_blocked_fn = check_blocked_fn
        self.agent_tools_policy = agent_tools_policy
        self.policy_retriever = policy_retriever
        self.policy_evaluator = policy_evaluator
        self.facts_retriever_fn = facts_retriever_fn
        self.supervisor = SupervisorAgent(
            model,
            policy_retriever=policy_retriever,
            policy_evaluator=policy_evaluator,
            facts_retriever_fn=facts_retriever_fn,
            check_blocked_fn=check_blocked_fn,
        )
        self.checkpointer = checkpointer
        self.workflow = create_banking_workflow(
            self.supervisor,
            self.cards_agent,
            self.fincrime_agent,
            checkpointer=checkpointer,
            policy=self.agent_tools_policy,
        )

    def route(self, history: list, collected: dict, allowed_tools: dict | None = None) -> str:
        """Dynamic routing mediated by the Supervisor classifier."""
        # Check tool restriction overrides
        if allowed_tools:
            non_flow = set(allowed_tools.keys()) - {'logout', 'login', 'register'}
            if non_flow and non_flow.issubset({'block_card', 'unblock_card'}):
                return 'fincrime'
            if non_flow and not any(t in non_flow for t in ('block_card', 'unblock_card')):
                return 'cards'

        user_text = next((t['content'] for t in reversed(history) if t.get('role') == 'user'), '')
        target, _, _ = self._sync_classify(user_text, allowed_tools or {}, collected)
        return 'fincrime' if 'fincrime' in target else 'cards'

    def _sync_classify(self, user_text: str, allowed_tools: dict, collected: dict) -> tuple[str, list[str], str | None]:
        lower = user_text.lower()
        if any(w in lower for w in ('unblock', 'un-block', 'unfreeze', 'un-freeze', 'reactivate', 'restore')):
            return 'fincrime_agent', ['unblock_card'], None
        if any(w in lower for w in ('block', 'freeze', 'stop card', 'lost', 'stolen', 'fraud', 'compromised')):
            return 'fincrime_agent', ['block_card'], None
        if collected.get('tool') in ('block_card', 'unblock_card'):
            return 'fincrime_agent', [collected['tool']], None
        if collected.get('tool') in ('create_card', 'issue_card', 'create_account', 'view_accounts', 'view_cards'):
            return 'cards_agent', [collected['tool']], None
        return 'cards_agent', ['view_accounts'], None

    async def interpret_turn(
        self,
        history: list,
        collected: dict,
        allowed_tools: dict,
        thread_id: str | None = None,
        workflow_id: str | None = None,
        session_id: str | None = None,
        owner: str | None = None,
        country_context: dict | None = None,
    ) -> CustomerReply | ProposedToolCall:
        """Execute a LangGraph multi-agent reasoning turn."""
        policy = self.agent_tools_policy
        if policy is None:
            try:
                from card_chat.mcp_server import get_agent_tools_policy
                policy = get_agent_tools_policy()
            except Exception:
                policy = None

        stable_thread = thread_id or f"{owner or 'cust'}:{session_id or uuid4().hex}"

        # Inspect checkpointer for prior state within this thread
        prior_state: dict = {}
        config = {"configurable": {"thread_id": stable_thread}}
        if self.checkpointer:
            try:
                snap = await self.workflow.aget_state(config)
                if snap and snap.values:
                    prior_state = snap.values
            except Exception:
                pass

        # Build LangGraph workflow state, preserving multi-turn context
        initial_state = {
            'country_context': country_context or prior_state.get('country_context', {}),
            'geographic_notice': None,
            'geographic_pending_intents': prior_state.get('geographic_pending_intents', []),
            "owner": owner or prior_state.get("owner", "customer"),
            "session_id": session_id or prior_state.get("session_id", "default"),
            "workflow_id": workflow_id or prior_state.get("workflow_id", stable_thread),
            "messages": history,
            "verified_draft": collected,
            "allowed_tools": allowed_tools,
            "handoff_history": [],
            "iteration_count": 0,
            "active_agent": None,
            "planned_intents": prior_state.get("planned_intents", []),
            "completed_intents": prior_state.get("completed_intents", []),
            "action_plan": prior_state.get("action_plan", []),
            "pending_customer_question": prior_state.get("pending_customer_question"),
            "pinned_policies": prior_state.get("pinned_policies", {}),
            "policy_planning": None,
            "verified_domain_facts": prior_state.get("verified_domain_facts", {}),
            # Domain events are consumed during execution resumption, not replayed
            # as fresh events when another customer message starts a graph run.
            "last_domain_event": None,
            "proposed_tool": None,
            "final_response": None,
        }

        # Run compiled LangGraph StateGraph with durable customer-scoped thread
        final_state = await self.workflow.ainvoke(initial_state, config=config)

        # Check if the workflow produced a tool proposal
        proposal = final_state.get("proposed_tool")
        if proposal and isinstance(proposal, dict):
            tool_name = proposal.get("name")
            tool_args = proposal.get("arguments", {})

            # Cross-agent dependency check:
            # If Cards agent proposes to issue/create a card, check if the account already has a blocked card
            if tool_name in ('issue_card', 'create_card'):
                account_num = tool_args.get('account_number') or collected.get('arguments', {}).get('account_number')
                if account_num and self.check_blocked_fn:
                    is_blocked = await self.check_blocked_fn(account_num)
                    if is_blocked:
                        return CustomerReply(
                            'Cannot issue a new card for this account because it has an active blocked card. '
                            'Under banking security rules, the blocked card must be unblocked or resolved first.'
                        )

            return ProposedToolCall(tool_name, tool_args)

        # Otherwise return customer reply
        reply_text = final_state.get("final_response") or "How can I help you today?"
        return CustomerReply(reply_text)

    async def resume_execution(
        self,
        thread_id: str,
        owner: str,
        session_id: str,
        tool_name: str,
        result: dict | Any,
        ok: bool = True,
        error: str | None = None,
        message: str = "",
        arguments: dict | None = None,
        allowed_tools: dict | None = None,
    ) -> CustomerReply | ProposedToolCall | None:
        """Resume LangGraph workflow with actual execution result from the trusted execution layer."""
        config = {"configurable": {"thread_id": thread_id}}
        if not self.checkpointer:
            return None

        try:
            state_snap = await self.workflow.aget_state(config)
        except Exception:
            state_snap = None

        # Verify isolation before both ordinary resume and a direct-form completion.
        previous = state_snap.values if state_snap else {}
        if owner and previous.get("owner") and previous["owner"] != owner:
            raise PermissionError("Cross-customer workflow access denied.")
        if not state_snap or not state_snap.next:
            if not (ok and tool_name == 'block_card' and arguments is not None and allowed_tools is not None):
                return None
            # A secure-form action can complete without an earlier model turn.
            # Seed only its verified completion, never a new executable proposal.
            data = result.get('result', {}) if isinstance(result, dict) else {}
            reason = data.get('last_block_reason') or data.get('reason') or arguments.get('reason', '')
            state = {**previous, 'owner': owner, 'session_id': session_id, 'workflow_id': thread_id,
                     'verified_draft': {'tool': tool_name, 'arguments': arguments},
                     'allowed_tools': allowed_tools, 'planned_intents': [], 'iteration_count': 0,
                     'pending_customer_question': None, 'active_agent': 'fincrime_agent',
                     'proposed_tool': None, 'last_domain_event': 'card_blocked_success',
                     'action_plan': [{'intent': tool_name, 'target_agent': 'fincrime_agent',
                         'status': 'completed', 'tool_proposal': {'name': tool_name, 'arguments': arguments},
                         'result': data, 'error': None}],
                     'handoff_history': [HandoffResult(source_agent='fincrime_agent', status='success',
                                                     message=message, data=data)]}
            facts = await self.supervisor._retrieve_domain_facts(state, tool_name)
            facts.update(has_blocked_card=True,
                         blocked_card_id=data.get('card_id') or data.get('id') or arguments.get('card_id'),
                         last_block_reason=reason,
                         is_temporary_freeze=any(word in reason.lower() for word in ('freeze', 'frozen', 'temporary')),
                         is_permanently_compromised=any(word in reason.lower() for word in ('lost','stolen','fraud','compromised','destroyed')))
            state['verified_domain_facts'] = facts
            update = await self.supervisor.plan_or_route(state)
            await self.workflow.aupdate_state(config, {**state, **update}, as_node='supervisor')
            return CustomerReply(update.get('final_response') or message)

        resume_payload = {
            "status": "confirmed",
            "ok": ok,
            "tool": tool_name,
            "data": result.get("result") if isinstance(result, dict) else result,
            "message": message,
            "error": error,
            "arguments": arguments,
        }

        from langgraph.types import Command
        try:
            final_state = await self.workflow.ainvoke(Command(resume=resume_payload), config=config)
        except Exception as exc:
            logger.exception("Failed to resume LangGraph workflow for thread %s: %s", thread_id, exc)
            return None

        proposal = final_state.get("proposed_tool")
        if proposal and isinstance(proposal, dict):
            return ProposedToolCall(proposal.get("name"), proposal.get("arguments", {}))

        final_response = final_state.get("final_response")
        if final_response:
            return CustomerReply(final_response)

        return None

    async def cancel_execution(
        self,
        thread_id: str,
        owner: str,
        session_id: str,
    ) -> CustomerReply | None:
        """Cancel LangGraph workflow during human confirmation."""
        config = {"configurable": {"thread_id": thread_id}}
        if not self.checkpointer:
            return None

        try:
            state_snap = await self.workflow.aget_state(config)
        except Exception:
            state_snap = None

        if not state_snap or not state_snap.next:
            return None

        if owner and state_snap.values.get("owner") and state_snap.values.get("owner") != owner:
            logger.warning("Unauthorized cancel attempt on workflow %s by owner %s", thread_id, owner)
            raise PermissionError("Cross-customer workflow access denied.")

        from langgraph.types import Command
        try:
            final_state = await self.workflow.ainvoke(Command(resume={"status": "rejected"}), config=config)
            return CustomerReply(final_state.get("final_response") or "Workflow cancelled.")
        except Exception as exc:
            logger.exception("Failed to cancel LangGraph workflow for thread %s: %s", thread_id, exc)
            return None
