"""LLM-powered LangGraph Supervisor Agent for dynamic routing and compound workflow synthesis."""
import json
import logging
import re
from typing import Any

from card_chat.orchestration.contracts import AgentHandoff, HandoffResult
from card_chat.orchestration.state import BankingWorkflowState
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.model_context import sanitise
from card_chat.observability import emit_agent_trace

logger = logging.getLogger(__name__)

MAX_SUPERVISOR_ITERATIONS = 5

FINCRIME_CAPABILITIES = {"block_card", "unblock_card"}
CARDS_CAPABILITIES = {
    "create_account",
    "view_accounts",
    "view_cards",
    "issue_card",
    "create_card",
    "register",
    "login",
    "logout",
}
ALL_CAPABILITIES = FINCRIME_CAPABILITIES | CARDS_CAPABILITIES


def get_target_agent_for_intent(intent: str) -> str:
    """Enforce domain ownership independently of LLM output."""
    if intent in FINCRIME_CAPABILITIES:
        return "fincrime_agent"
    return "cards_agent"


def _find_keyword_pos(text: str, keywords: tuple[str, ...]) -> int:
    """Helper to find earliest keyword occurrence using word boundaries."""
    best_pos = -1
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        match = re.search(pattern, text)
        if match:
            idx = match.start()
            if best_pos == -1 or idx < best_pos:
                best_pos = idx
    return best_pos


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

        # Check if we are resuming after a specialist result in this execution cycle
        active_agent = state.get("active_agent") or (handoff_history[-1].source_agent if handoff_history else None)
        if handoff_history and active_agent:
            last_result = handoff_history[-1]
            if last_result.status == "requires_confirmation" and last_result.tool_proposal:
                # If a specialist proposed a sensitive action needing confirmation, route to policy/human confirmation
                return {
                    "iteration_count": iterations,
                    "active_agent": None,
                    "next_step": "human_confirmation",
                    "proposed_tool": last_result.tool_proposal,
                    "final_response": last_result.message,
                }
            if last_result.status == "success":
                if planned_intents:
                    # Compound workflow: previous specialist succeeded; resume next planned intent
                    next_intent = planned_intents.pop(0)
                    completed_intents.append(next_intent)
                    target_agent = get_target_agent_for_intent(next_intent)
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
                        "active_agent": None,
                        "next_step": "respond",
                        "final_response": last_result.message,
                    }
            if last_result.status == "failed":
                return {
                    "iteration_count": iterations,
                    "active_agent": None,
                    "next_step": "respond",
                    "final_response": last_result.message,
                }

        # Analyze intent and detect compound requests
        target_agent, detected_intents, direct_reply = await self._classify_and_plan(
            user_text, allowed_tools, state.get("verified_draft", {})
        )

        if direct_reply or not detected_intents:
            return {
                "iteration_count": iterations,
                "next_step": "respond",
                "final_response": direct_reply or "Could you please clarify what banking service you would like to perform?",
            }

        # If compound intents were detected (e.g. block_card + issue_card)
        primary_intent = detected_intents[0]
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
        """Classify user intent and detect compound requests using LLM first, with rule fallback."""
        clean_text = user_text.strip()
        lower = clean_text.lower()
        draft_tool = verified_draft.get("tool")

        # 1. Deterministic routing for explicit UI selections / verified drafts
        # When user explicitly selected an action in UI (empty text or action name/confirmation)
        if draft_tool and (
            not clean_text
            or lower in (
                draft_tool.lower(),
                draft_tool.replace("_", " ").lower(),
                "confirm",
                "proceed",
                "yes",
                "submit",
                "ok",
                "select",
            )
        ):
            if draft_tool in allowed_tools:
                target = get_target_agent_for_intent(draft_tool)
                return target, [draft_tool], None
            return (
                "cards_agent",
                [],
                "The selected action is not authorized or is currently unavailable.",
            )

        # 2. Outbound PII protection before any external model invocation
        from card_chat.pii.gateway import get_default_pii_gateway
        gateway = get_default_pii_gateway()
        if gateway and clean_text:
            gateway.verify_outbound(clean_text)

        # 3. LLM-based intent classification and compound-request planning
        if (
            hasattr(self.model, "client")
            and hasattr(self.model.client, "messages")
            and getattr(self.model, "model", "") not in ("synthetic-model", "stub")
        ):
            try:
                system_prompt = (
                    "You are a banking supervisor orchestrator. Analyze the customer's natural language request and classify into "
                    "one or more specialized banking intents in execution order.\n"
                    "Available banking capabilities:\n"
                    "- FinCrime domain (fincrime_agent): block_card, unblock_card\n"
                    "- Cards domain (cards_agent): create_account, view_accounts, view_cards, issue_card, create_card, register, login, logout\n"
                    "For compound requests (e.g. 'block my card and issue a replacement'), list all intents in order.\n"
                    "If the customer's request is ambiguous or unsupported, set intents to empty list and provide a clarification reply.\n"
                    "Respond with a JSON object ONLY: {\"intents\": [\"intent1\", ...], \"reply\": null or \"clarification message\"}."
                )
                response = await self.model.client.messages.create(
                    model=self.model.model,
                    max_tokens=250,
                    system=[{"type": "text", "text": system_prompt}],
                    messages=[{"role": "user", "content": clean_text}],
                )
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                data = json.loads(text)
                raw_intents = data.get("intents", [])
                if isinstance(raw_intents, str):
                    raw_intents = [raw_intents]

                llm_reply = data.get("reply")
                if not raw_intents:
                    return "cards_agent", [], llm_reply or "Could you please clarify what banking service you require?"

                # Validate against strict capability allowlist
                valid_intents = [i for i in raw_intents if i in ALL_CAPABILITIES or i in allowed_tools]
                if not valid_intents:
                    return (
                        "cards_agent",
                        [],
                        "I cannot fulfill this request as it is not a supported banking service. I can help you view accounts, manage cards, or report a lost card.",
                    )

                # Filter against caller's authorized tools
                authorized_intents = [i for i in valid_intents if i in allowed_tools]
                if not authorized_intents:
                    return (
                        "cards_agent",
                        [],
                        "You do not have authorization to perform the requested action, or that capability is currently unavailable.",
                    )

                primary = authorized_intents[0]
                # Enforce domain ownership independently of LLM output
                target = get_target_agent_for_intent(primary)
                return target, authorized_intents, None
            except Exception as e:
                logger.info("Supervisor LLM intent classification failed or not JSON; falling back to deterministic semantic rules: %s", e)

        # 4. Deterministic semantic rule fallback
        has_card = any(w in lower for w in ("card", "cards", "debit", "credit"))
        has_account = any(w in lower for w in ("account", "accounts", "balance"))

        intent_candidates: list[tuple[str, int]] = []

        # Auth
        pos_reg = _find_keyword_pos(lower, ("register", "sign up", "signup"))
        if pos_reg != -1:
            intent_candidates.append(("register", pos_reg))

        pos_login = _find_keyword_pos(lower, ("login", "sign in", "signin", "log in", "log me in"))
        if pos_login != -1:
            intent_candidates.append(("login", pos_login))

        pos_logout = _find_keyword_pos(lower, ("logout", "sign out", "signout", "log out", "log me out"))
        if pos_logout != -1:
            intent_candidates.append(("logout", pos_logout))

        # Unblock card
        pos_unblock = _find_keyword_pos(lower, ("unblock", "un-block", "unfreeze", "un-freeze", "reactivate", "restore"))
        if pos_unblock != -1 and (has_card or any(w in lower for w in ("unblock", "unfreeze", "reactivate"))):
            intent_candidates.append(("unblock_card", pos_unblock))

        # Block card
        pos_block = _find_keyword_pos(
            lower,
            ("block", "freeze", "stop", "lost", "stolen", "fraud", "compromised"),
        )
        if pos_block != -1 and (has_card or any(w in lower for w in ("lost", "stolen", "fraud", "compromised", "freeze"))):
            intent_candidates.append(("block_card", pos_block))

        # Issue / create card
        pos_issue = _find_keyword_pos(
            lower,
            ("issue", "new card", "debit card", "credit card", "replacement", "replace", "another card", "additional card", "create card", "create a card", "create the card", "order a card", "want a card", "want a new"),
        )
        if pos_issue != -1 and (has_card or "replacement" in lower or "replace" in lower):
            intent_candidates.append(("issue_card", pos_issue))

        # Create account
        pos_create_acc = _find_keyword_pos(lower, ("create account", "open account", "new account", "create an account", "open an account"))
        if pos_create_acc != -1 and has_account:
            intent_candidates.append(("create_account", pos_create_acc))

        # View cards (requires explicit viewing action verbs, never just "my card" to avoid false positive in "lost my card")
        pos_view_card = _find_keyword_pos(
            lower,
            ("view card", "view cards", "view my card", "show card", "show cards", "show my card", "list card", "list cards", "see card", "see cards", "display card", "display cards"),
        )
        if pos_view_card != -1 and has_card:
            intent_candidates.append(("view_cards", pos_view_card))

        # View accounts (requires explicit viewing action verbs)
        pos_view_acc = _find_keyword_pos(
            lower,
            ("view account", "view accounts", "view my account", "show account", "show accounts", "show my accounts", "list account", "list accounts", "see account", "see accounts", "display account", "display accounts", "account balance", "balance", "my accounts"),
        )
        if pos_view_acc != -1 and has_account:
            intent_candidates.append(("view_accounts", pos_view_acc))

        # Sort detected intents by their position in user text to preserve compound ordering
        intent_candidates.sort(key=lambda x: x[1])
        detected_intents = [item[0] for item in intent_candidates]

        # Deduplicate while preserving order
        ordered_unique: list[str] = []
        for i in detected_intents:
            if i not in ordered_unique:
                ordered_unique.append(i)
        detected_intents = ordered_unique

        # If model outputs were pre-configured in test harness
        if not detected_intents and hasattr(self.model, "outputs") and self.model.outputs:
            output = self.model.outputs[0]
            if isinstance(output, ProposedToolCall):
                detected_intents = [output.name]
            elif isinstance(output, CustomerReply):
                reply_lower = output.text.lower()
                for tool in ALL_CAPABILITIES:
                    if tool in reply_lower or tool.replace("_", " ") in reply_lower:
                        detected_intents.append(tool)

        # If intent candidates were recognized from text
        if detected_intents:
            # Filter against authorized tools
            authorized = [i for i in detected_intents if i in allowed_tools]
            if not authorized:
                return (
                    "cards_agent",
                    [],
                    "You do not have authorization to perform the requested action, or that capability is currently unavailable.",
                )
            primary = authorized[0]
            target = get_target_agent_for_intent(primary)
            return target, authorized, None

        # Check existing draft facts if user did not specify a new intent
        if draft_tool and draft_tool in allowed_tools:
            target = get_target_agent_for_intent(draft_tool)
            return target, [draft_tool], None

        # Greetings or polite queries - ask friendly clarification rather than defaulting to view_accounts
        if any(w in lower for w in ("hello", "hi", "hey")):
            return "cards_agent", [], "Hello! I can help you manage your accounts, cards, or report a lost card. How can I help you today?"

        # Fallback when intent cannot be confidently determined: Ask for clarification!
        return "cards_agent", [], "I'm not sure what you'd like to do. Would you like to view your accounts, manage cards, or report a lost card?"

