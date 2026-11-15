"""Multi-agent orchestration and specialized agents for card servicing and FinCrime using LangGraph."""
import logging
from uuid import uuid4
from typing import Any

from card_chat.provider import Model
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.model_context import build_context
from card_chat.orchestration.supervisor import SupervisorAgent
from card_chat.orchestration.graph import create_banking_workflow

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
                 checkpointer=None):
        self.model = model
        self.cards_agent = cards_agent or CardServicingAgent(model)
        self.fincrime_agent = fincrime_agent or FinCrimeAgent(model)
        self.check_blocked_fn = check_blocked_fn
        self.agent_tools_policy = agent_tools_policy
        self.supervisor = SupervisorAgent(model)
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

        # Build initial LangGraph workflow state
        initial_state = {
            "owner": owner or "customer",
            "session_id": session_id or "default",
            "workflow_id": workflow_id or stable_thread,
            "messages": history,
            "verified_draft": collected,
            "allowed_tools": allowed_tools,
            "handoff_history": [],
            "iteration_count": 0,
        }

        # Run compiled LangGraph StateGraph with durable customer-scoped thread
        config = {"configurable": {"thread_id": stable_thread}}
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
