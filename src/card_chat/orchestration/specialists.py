"""Specialist agents (CardsAgent and FinCrimeAgent) implemented as LangGraph reasoning nodes."""
import logging
from typing import Any

from card_chat.orchestration.contracts import AgentHandoff, HandoffResult
from card_chat.orchestration.state import BankingWorkflowState
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.mcp_server import get_agent_tools_policy

logger = logging.getLogger(__name__)


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
        if verified_draft.get("tool") == intent:
            context_arguments = verified_draft.get("arguments", {})
        else:
            context_arguments = handoff.relevant_context if handoff else {}

        draft = {"tool": intent, "arguments": context_arguments} if intent else verified_draft

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
            return {
                "handoff_history": [result],
                "final_response": turn.text,
                "next_step": "supervisor",
            }
        elif isinstance(turn, ProposedToolCall):
            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message=f"I have prepared {turn.name} for your review.",
                tool_proposal={"name": turn.name, "arguments": turn.arguments},
            )
            return {
                "handoff_history": [result],
                "proposed_tool": {"name": turn.name, "arguments": turn.arguments},
                "next_step": "supervisor",
            }

        result = HandoffResult(source_agent=self.name, status="failed", message="Unknown turn response")
        return {"handoff_history": [result], "next_step": "supervisor"}


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
        if verified_draft.get("tool") == intent:
            context_arguments = verified_draft.get("arguments", {})
        else:
            context_arguments = handoff.relevant_context if handoff else {}

        draft = {"tool": intent, "arguments": context_arguments} if intent else verified_draft

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
            return {
                "handoff_history": [result],
                "final_response": turn.text,
                "next_step": "supervisor",
            }
        elif isinstance(turn, ProposedToolCall):
            result = HandoffResult(
                source_agent=self.name,
                status="requires_confirmation",
                message=f"I have prepared {turn.name} for your review.",
                tool_proposal={"name": turn.name, "arguments": turn.arguments},
            )
            return {
                "handoff_history": [result],
                "proposed_tool": {"name": turn.name, "arguments": turn.arguments},
                "next_step": "supervisor",
            }

        result = HandoffResult(source_agent=self.name, status="failed", message="Unknown turn response")
        return {"handoff_history": [result], "next_step": "supervisor"}
