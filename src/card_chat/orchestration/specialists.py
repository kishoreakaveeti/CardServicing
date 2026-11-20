"""Specialist agents (CardsAgent and FinCrimeAgent) implemented as LangGraph reasoning nodes."""
import logging
from typing import Any

from card_chat.orchestration.contracts import AgentHandoff, HandoffResult
from card_chat.orchestration.state import BankingWorkflowState
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.mcp_server import get_agent_tools_policy
from card_chat.model_context import build_context

logger = logging.getLogger(__name__)


def _is_unconfigured_stub(agent: Any) -> bool:
    """Check if an agent is a local stub model without a live client or pre-queued outputs."""
    model = getattr(agent, "model", None)
    if model is None:
        return False
    if hasattr(model, "client"):
        return False
    if hasattr(model, "outputs") and model.outputs:
        return False
    return True


def filter_tools_for_agent(agent_name: str, allowed_tools: dict, policy: dict | None = None) -> dict:
    """Filter tools according to the declarative agent-tool policy in tools.json."""
    if policy is None:
        try:
            policy = get_agent_tools_policy()
        except Exception:
            policy = None

    if policy and agent_name in policy:
        agent_allowed_names = set(policy[agent_name])
        agent_allowed_names.update(['login', 'register', 'logout'])
        all_configured = set().union(*policy.values())
        if agent_name == 'cards_agent':
            for tool_name in allowed_tools:
                if tool_name not in all_configured:
                    agent_allowed_names.add(tool_name)
        return {k: v for k, v in allowed_tools.items() if k in agent_allowed_names}
    return allowed_tools


class CardsSpecialistNode:
    """Specialist agent for account lifecycle, viewing accounts/cards, and new card issuance."""

    def __init__(self, agent_instance: Any, policy: dict | None = None):
        self.agent = agent_instance
        self.name = "cards_agent"
        self.policy = policy

    async def execute(self, state: BankingWorkflowState) -> dict[str, Any]:
        handoff = state.get("current_handoff")
        allowed_tools = state.get("allowed_tools", {})
        verified_draft = state.get("verified_draft", {})
        messages = state.get("messages", [])

        # Filter tools to Cards servicing domain using declarative policy
        cards_tools = filter_tools_for_agent(self.name, allowed_tools, self.policy)

        # Build minimal context for specialist turn
        intent = handoff.intent if handoff else verified_draft.get("tool")
        if intent == "inquire":
            intent = None
        if verified_draft.get("tool") == intent:
            context_arguments = verified_draft.get("arguments", {})
        else:
            context_arguments = handoff.relevant_context if handoff else {}

        draft = {"tool": intent, "arguments": context_arguments} if intent else verified_draft

        # Fail-closed outbound PII verification before specialist invocation
        from card_chat.pii.gateway import get_default_pii_gateway
        gateway = get_default_pii_gateway()
        if gateway:
            safe_messages, safe_draft, _ = build_context(messages, draft, cards_tools)
            gateway.verify_outbound(str(safe_messages) + " " + str(safe_draft))

        # Deterministic create_account proposal if model has no queued outputs
        if intent == "create_account" and _is_unconfigured_stub(self.agent):
            last_user_text = next((m.get("content", "").lower() for m in reversed(messages) if m.get("role") == "user"), "")
            acc_type = context_arguments.get("account_type")
            if not acc_type:
                if "saving" in last_user_text:
                    acc_type = "savings"
                else:
                    acc_type = "transaction"
            issue_c = context_arguments.get("issue_card")
            if issue_c is None:
                issue_c = True
            tool_args = {
                "account_type": acc_type,
                "issue_card": bool(issue_c),
            }
            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message="I have prepared create_account for your review.",
                tool_proposal={"name": "create_account", "arguments": tool_args},
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "proposed_tool": {"name": "create_account", "arguments": tool_args},
                "next_step": "supervisor",
            }

        # Deterministic issue_card / create_card proposal if model has no queued outputs
        if intent in ("issue_card", "create_card") and _is_unconfigured_stub(self.agent):
            target_acc = (
                context_arguments.get("account_number")
                or verified_draft.get("arguments", {}).get("account_number")
                or state.get("verified_domain_facts", {}).get("account_number")
                or (handoff.relevant_context.get("account_number") if handoff and handoff.relevant_context else None)
            )
            tool_args = {"account_number": target_acc} if target_acc else {}
            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message=f"I have prepared {intent} for your review.",
                tool_proposal={"name": intent, "arguments": tool_args},
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "proposed_tool": {"name": intent, "arguments": tool_args},
                "next_step": "supervisor",
            }

        # The supervisor has classified replacement and checked its policies.
        # Prepare its existing secure form directly; another model turn must not
        # ask again for a country that was already supplied and validated.
        if intent == "replace_card":
            target_card_id = (
                context_arguments.get("card_id")
                or verified_draft.get("arguments", {}).get("card_id")
                or state.get("verified_domain_facts", {}).get("blocked_card_id")
                or (handoff.relevant_context.get("card_id") if handoff and handoff.relevant_context else None)
            )
            reason_val = context_arguments.get("reason") or "lost card replacement"
            tool_args = {**({'card_id': target_card_id} if target_card_id else {}), 'reason': reason_val}
            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message="I have prepared replace_card for your review.",
                tool_proposal={"name": "replace_card", "arguments": tool_args},
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "proposed_tool": {"name": "replace_card", "arguments": tool_args},
                "next_step": "supervisor",
            }


        try:
            turn = await self.agent.interpret_turn(messages, draft, cards_tools)
        except Exception as e:
            logger.exception("Cards specialist turn failed: %s", e)
            result = HandoffResult(
                source_agent=self.name,
                status="failed",
                message="The card servicing specialist encountered an error. Please try again.",
            )
            return {"handoff_history": [result], "next_step": "supervisor"}

        if isinstance(turn, CustomerReply):
            result = HandoffResult(
                source_agent=self.name,
                status="success",
                message=turn.text,
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "final_response": turn.text,
                "next_step": "supervisor",
            }
        elif isinstance(turn, ProposedToolCall):
            # Strict domain boundary verification
            if turn.name not in cards_tools:
                logger.error("Cards specialist attempted unauthorized tool: %s", turn.name)
                result = HandoffResult(
                    source_agent=self.name,
                    status="failed",
                    message=f"Unauthorized tool {turn.name} blocked by specialist policy.",
                )
                history = list(state.get("handoff_history", [])) + [result]
                return {"handoff_history": history, "active_agent": self.name, "next_step": "supervisor"}

            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message=f"I have prepared {turn.name} for your review.",
                tool_proposal={"name": turn.name, "arguments": turn.arguments},
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "proposed_tool": {"name": turn.name, "arguments": turn.arguments},
                "next_step": "supervisor",
            }

        result = HandoffResult(source_agent=self.name, status="failed", message="Unknown turn response")
        history = list(state.get("handoff_history", [])) + [result]
        return {"handoff_history": history, "active_agent": self.name, "next_step": "supervisor"}


class FinCrimeSpecialistNode:
    """Specialist agent for financial crime protection, risk management, and card blocking/unblocking."""

    def __init__(self, agent_instance: Any, policy: dict | None = None):
        self.agent = agent_instance
        self.name = "fincrime_agent"
        self.policy = policy

    async def execute(self, state: BankingWorkflowState) -> dict[str, Any]:
        handoff = state.get("current_handoff")
        allowed_tools = state.get("allowed_tools", {})
        verified_draft = state.get("verified_draft", {})
        messages = state.get("messages", [])

        # Filter tools to FinCrime risk domain using declarative policy
        fincrime_tools = filter_tools_for_agent(self.name, allowed_tools, self.policy)

        # Build minimal context for specialist turn
        intent = handoff.intent if handoff else verified_draft.get("tool")
        if intent == "inquire":
            intent = None
        if verified_draft.get("tool") == intent:
            context_arguments = verified_draft.get("arguments", {})
        else:
            context_arguments = handoff.relevant_context if handoff else {}

        draft = {"tool": intent, "arguments": context_arguments} if intent else verified_draft

        # Fail-closed outbound PII verification before specialist invocation
        from card_chat.pii.gateway import get_default_pii_gateway
        gateway = get_default_pii_gateway()
        if gateway:
            safe_messages, safe_draft, _ = build_context(messages, draft, fincrime_tools)
            gateway.verify_outbound(str(safe_messages) + " " + str(safe_draft))

        # Deterministic unblock_card proposal if model has no queued outputs
        if intent == "unblock_card" and _is_unconfigured_stub(self.agent):
            target_card_id = (
                context_arguments.get("card_id")
                or verified_draft.get("arguments", {}).get("card_id")
                or state.get("verified_domain_facts", {}).get("blocked_card_id")
                or (handoff.relevant_context.get("card_id") if handoff and handoff.relevant_context else None)
            )
            tool_args = {"reason": context_arguments.get("reason", "Customer requested unblock")}
            if target_card_id:
                tool_args["card_id"] = target_card_id
            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message="I have prepared unblock_card for your review.",
                tool_proposal={"name": "unblock_card", "arguments": tool_args},
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "proposed_tool": {"name": "unblock_card", "arguments": tool_args},
                "next_step": "supervisor",
            }

        # Deterministic block_card proposal if card_id is known and model has no queued outputs
        if intent == "block_card" and ((_is_unconfigured_stub(self.agent) and context_arguments.get("card_id"))
                or state.get('planned_intents')):
            tool_args = {"reason": context_arguments.get("reason", "Customer requested blocking")}
            if context_arguments.get('card_id'):
                tool_args['card_id'] = context_arguments['card_id']
            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message="I have prepared block_card for your review.",
                tool_proposal={"name": "block_card", "arguments": tool_args},
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "proposed_tool": {"name": "block_card", "arguments": tool_args},
                "next_step": "supervisor",
            }

        try:
            turn = await self.agent.interpret_turn(messages, draft, fincrime_tools)
        except Exception as e:
            logger.exception("FinCrime specialist turn failed: %s", e)
            result = HandoffResult(
                source_agent=self.name,
                status="failed",
                message="The financial crime specialist encountered an error. Please try again.",
            )
            return {"handoff_history": [result], "next_step": "supervisor"}

        if isinstance(turn, CustomerReply):
            result = HandoffResult(
                source_agent=self.name,
                status="success",
                message=turn.text,
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "final_response": turn.text,
                "next_step": "supervisor",
            }
        elif isinstance(turn, ProposedToolCall):
            # Strict domain boundary verification
            if turn.name not in fincrime_tools:
                logger.error("FinCrime specialist attempted unauthorized tool: %s", turn.name)
                result = HandoffResult(
                    source_agent=self.name,
                    status="failed",
                    message=f"Unauthorized tool {turn.name} blocked by specialist policy.",
                )
                history = list(state.get("handoff_history", [])) + [result]
                return {"handoff_history": history, "active_agent": self.name, "next_step": "supervisor"}

            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message=f"I have prepared {turn.name} for your review.",
                tool_proposal={"name": turn.name, "arguments": turn.arguments},
            )
            history = list(state.get("handoff_history", [])) + [result]
            return {
                "handoff_history": history,
                "active_agent": self.name,
                "proposed_tool": {"name": turn.name, "arguments": turn.arguments},
                "next_step": "supervisor",
            }

        result = HandoffResult(source_agent=self.name, status="failed", message="Unknown turn response")
        history = list(state.get("handoff_history", [])) + [result]
        return {"handoff_history": history, "active_agent": self.name, "next_step": "supervisor"}
