"""LLM-powered LangGraph Supervisor Agent for dynamic routing and compound workflow synthesis."""
import json
import logging
from typing import Any

from card_chat.orchestration.contracts import AgentHandoff, HandoffResult
from card_chat.orchestration.state import BankingWorkflowState
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.model_context import sanitise
from card_chat.observability import emit_agent_trace

logger = logging.getLogger(__name__)

MAX_SUPERVISOR_ITERATIONS = 5


class SupervisorAgent:
    """LangGraph Supervisor Agent orchestrating specialist delegation, compound requests,

    and multi-turn resumption using Anthropic Claude or deterministic stub.
    """

    def __init__(self, model: Any):
        self.model = model
        self.name = "supervisor"

    async def plan_or_route(self, state: BankingWorkflowState) -> dict[str, Any]:
        """Supervisor node entry point in the LangGraph workflow."""
        iterations = state.get("iteration_count", 0) + 1
        if iterations > MAX_SUPERVISOR_ITERATIONS:
            logger.warning("Supervisor exceeded max iterations (%d); terminating loop", iterations)
            return {
                "iteration_count": iterations,
                "next_step": "respond",
                "final_response": "I could not complete all steps of your request within the safety limit. Please clarify your request.",
            }

        messages = state.get("messages", [])
        planned_intents = list(state.get("planned_intents", []))
        completed_intents = list(state.get("completed_intents", []))
        handoff_history = state.get("handoff_history", [])
        owner = state.get("owner", "customer")
        session_id = state.get("session_id", "default")
        workflow_id = state.get("workflow_id", "default")
        allowed_tools = state.get("allowed_tools", {})

        # Extract latest user message
        user_text = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )

        # Check if we are resuming after a specialist result
        if handoff_history:
            last_result = handoff_history[-1]
            if last_result.status == "requires_confirmation" and last_result.tool_proposal:
                # If a specialist proposed a sensitive action needing confirmation, route to policy/human confirmation
                return {
                    "iteration_count": iterations,
                    "next_step": "human_confirmation",
                    "proposed_tool": last_result.tool_proposal,
                    "final_response": last_result.message,
                }
            if last_result.status == "success":
                if planned_intents:
                    # Compound workflow: previous specialist succeeded; resume next planned intent
                    next_intent = planned_intents.pop(0)
                    completed_intents.append(next_intent)
                    target_agent = "fincrime_agent" if next_intent in ("block_card", "unblock_card") else "cards_agent"
                    handoff = AgentHandoff(
                        origin_agent="supervisor",
                        target_agent=target_agent,
                        intent=next_intent,
                        relevant_context={"previous_result": last_result.message},
                        session_id=session_id,
                        workflow_id=workflow_id,
                    )
                    return {
                        "iteration_count": iterations,
                        "planned_intents": planned_intents,
                        "completed_intents": completed_intents,
                        "current_handoff": handoff,
                        "active_agent": target_agent,
                        "next_step": target_agent,
                    }
                else:
                    return {
                        "iteration_count": iterations,
                        "next_step": "respond",
                        "final_response": last_result.message,
                    }
            if last_result.status == "failed":
                return {
                    "iteration_count": iterations,
                    "next_step": "respond",
                    "final_response": last_result.message,
                }

        # Analyze intent and detect compound requests
        target_agent, detected_intents, direct_reply = await self._classify_and_plan(
            user_text, allowed_tools, state.get("verified_draft", {})
        )

        if direct_reply:
            return {
                "iteration_count": iterations,
                "next_step": "respond",
                "final_response": direct_reply,
            }

        # If compound intents were detected (e.g. unblock + issue_card)
        primary_intent = detected_intents[0] if detected_intents else "view_accounts"
        remaining_intents = detected_intents[1:] if len(detected_intents) > 1 else []

        handoff = AgentHandoff(
            origin_agent="supervisor",
            target_agent=target_agent,
            intent=primary_intent,
            relevant_context=state.get("verified_draft", {}).get("arguments", {}),
            session_id=session_id,
            workflow_id=workflow_id,
        )

        emit_agent_trace(
            "supervisor_decision",
            target_agent=target_agent,
            primary_intent=primary_intent,
            compound_intents=remaining_intents,
            session_id=session_id,
        )
        emit_agent_trace(
            "agent_handoff",
            origin="supervisor",
            target=target_agent,
            intent=primary_intent,
            context_keys=list(state.get("verified_draft", {}).get("arguments", {}).keys()),
        )

        return {
            "iteration_count": iterations,
            "planned_intents": remaining_intents,
            "completed_intents": completed_intents,
            "current_handoff": handoff,
            "active_agent": target_agent,
            "next_step": target_agent,
        }

    async def _classify_and_plan(
        self,
        user_text: str,
        allowed_tools: dict[str, Any],
        verified_draft: dict[str, Any],
    ) -> tuple[str, list[str], str | None]:
        """Classify user intent and detect compound requests."""
        # Check tool restriction overrides
        non_flow = set(allowed_tools.keys()) - {"logout", "login", "register"}
        if non_flow and non_flow.issubset({"block_card", "unblock_card"}):
            return "fincrime_agent", ["block_card"], None
        if non_flow and not any(t in non_flow for t in ("block_card", "unblock_card")):
            return "cards_agent", ["view_accounts"], None

        lower = user_text.lower()

        # Collect all intents across specialists for compound request detection
        intents = []
        if any(w in lower for w in ("unblock", "un-block", "unfreeze", "un-freeze", "reactivate", "restore")):
            intents.append("unblock_card")
        elif any(w in lower for w in ("block", "freeze", "stop card", "lost", "stolen", "fraud", "compromised")):
            intents.append("block_card")

        if any(w in lower for w in ("issue", "create card", "new card", "another card", "additional card")):
            intents.append("issue_card")
        elif any(w in lower for w in ("create account", "open account", "new account")):
            intents.append("create_account")
        elif any(w in lower for w in ("view account", "show account", "list account", "see account", "my accounts", "account balance", "balance")):
            intents.append("view_accounts")
        elif any(w in lower for w in ("view card", "show card", "list card", "see card", "my cards", "view cards")):
            intents.append("view_cards")

        if intents:
            first = intents[0]
            target = "fincrime_agent" if first in ("block_card", "unblock_card") else "cards_agent"
            return target, intents, None

        # Check existing draft facts
        if verified_draft.get("tool"):
            tool = verified_draft["tool"]
            target = "fincrime_agent" if tool in ("block_card", "unblock_card") else "cards_agent"
            return target, [tool], None

        # For general or complex queries, use LLM if available and not a mock/test stub
        if hasattr(self.model, "client") and hasattr(self.model.client, "messages") and getattr(self.model, "model", "") not in ("synthetic-model",):
            try:
                system_prompt = (
                    "You are a banking supervisor orchestrator. Analyze the customer's request and classify into "
                    "one or more specialized banking intents: "
                    "Specialist 'fincrime_agent' handles: block_card, unblock_card, fraud mitigation. "
                    "Specialist 'cards_agent' handles: create_account, view_accounts, view_cards, issue_card. "
                    "Respond with a JSON object: {\"target\": \"fincrime_agent\"|\"cards_agent\"|\"direct\", "
                    "\"intents\": [\"intent1\", ...], \"reply\": null|\"reply string\"}."
                )
                response = await self.model.client.messages.create(
                    model=self.model.model,
                    max_tokens=250,
                    system=[{"type": "text", "text": system_prompt}],
                    messages=[{"role": "user", "content": user_text}],
                )
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                data = json.loads(text)
                return (
                    data.get("target", "cards_agent"),
                    data.get("intents", ["view_accounts"]),
                    data.get("reply"),
                )
            except Exception as e:
                logger.info("Supervisor LLM call failed or not JSON; falling back to semantic rules: %s", e)

        if any(w in lower for w in ("hello", "hi", "help", "hey")):
            return "cards_agent", ["view_accounts"], None

        return "cards_agent", ["view_accounts"], None
