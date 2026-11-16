"""LangGraph StateGraph multi-agent orchestration workflow with checkpointing."""
import logging
from typing import Any

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt
from card_chat.orchestration.persistence import DurableSqliteSaver
from card_chat.orchestration.contracts import HandoffResult

from card_chat.orchestration.state import BankingWorkflowState
from card_chat.orchestration.supervisor import SupervisorAgent
from card_chat.orchestration.specialists import CardsSpecialistNode, FinCrimeSpecialistNode

logger = logging.getLogger(__name__)


def route_supervisor_next(state: BankingWorkflowState) -> str:
    """Conditional edge router for supervisor decisions."""
    next_step = state.get("next_step")
    if next_step == "cards_agent":
        return "cards_agent"
    elif next_step == "fincrime_agent":
        return "fincrime_agent"
    elif next_step == "human_confirmation":
        return "human_confirmation"
    return END


async def human_confirmation_node(state: BankingWorkflowState) -> dict[str, Any]:
    """Checkpoint node representing an intentional pause for two-phase customer review."""
    proposal = state.get("proposed_tool")
    action_plan = list(state.get("action_plan", []))
    current_handoff = state.get("current_handoff")

    # Update action plan: current action is awaiting confirmation
    if action_plan and proposal:
        for item in action_plan:
            if item.get("status") in ("pending", "executing") and item.get("intent") == proposal.get("name"):
                item["status"] = "awaiting_confirmation"
                item["tool_proposal"] = proposal
                break

    # Pause execution and save checkpoint via LangGraph interrupt
    confirmation_payload = interrupt({
        "proposed_tool": proposal,
        "action_plan": action_plan,
        "can_confirm": True,
    })

    # Resumed via Command(resume=confirmation_payload)
    status = confirmation_payload.get("status", "confirmed") if isinstance(confirmation_payload, dict) else "confirmed"
    ok = confirmation_payload.get("ok", True) if isinstance(confirmation_payload, dict) else True
    exec_data = confirmation_payload.get("data") if isinstance(confirmation_payload, dict) else None
    exec_msg = confirmation_payload.get("message", "") if isinstance(confirmation_payload, dict) else ""
    exec_error = confirmation_payload.get("error") if isinstance(confirmation_payload, dict) else None
    tool_name = confirmation_payload.get("tool") or (proposal.get("name") if proposal else None)

    active_agent = state.get("active_agent") or (current_handoff.target_agent if current_handoff else "supervisor")

    if status == "rejected":
        # Customer rejected or cancelled confirmation
        if action_plan:
            for item in action_plan:
                if item.get("status") in ("awaiting_confirmation", "executing"):
                    item["status"] = "cancelled"
                elif item.get("status") == "pending":
                    item["status"] = "cancelled"
        result = HandoffResult(
            source_agent=active_agent,
            status="failed",
            message="Operation was cancelled by the customer.",
        )
        return {
            "can_confirm": False,
            "proposed_tool": None,
            "action_plan": action_plan,
            "handoff_history": list(state.get("handoff_history", [])) + [result],
            "active_agent": active_agent,
            "next_step": "supervisor",
        }

    if not ok:
        # Execution failed in trusted layer
        if action_plan:
            for item in action_plan:
                if item.get("status") in ("awaiting_confirmation", "executing"):
                    item["status"] = "failed"
                    item["error"] = exec_error or exec_msg
                elif item.get("status") == "pending":
                    item["status"] = "cancelled"
        result = HandoffResult(
            source_agent=active_agent,
            status="failed",
            message=exec_msg or exec_error or "Execution failed.",
        )
        return {
            "can_confirm": False,
            "proposed_tool": None,
            "action_plan": action_plan,
            "handoff_history": list(state.get("handoff_history", [])) + [result],
            "active_agent": active_agent,
            "next_step": "supervisor",
        }

    # Execution succeeded in trusted layer
    if action_plan:
        for item in action_plan:
            if item.get("status") in ("awaiting_confirmation", "executing") and (not tool_name or item.get("intent") == tool_name):
                item["status"] = "completed"
                item["result"] = exec_data
                break

    result = HandoffResult(
        source_agent=active_agent,
        status="success",
        message=exec_msg or f"{tool_name} completed successfully.",
        data=exec_data,
    )

    verified_facts = dict(state.get("verified_domain_facts", {}))
    last_event = state.get("last_domain_event")
    if tool_name == "block_card":
        last_event = "card_blocked_success"
        if proposal and proposal.get("arguments"):
            verified_facts["last_blocked_card_id"] = proposal["arguments"].get("card_id")
            verified_facts["blocked_card_id"] = proposal["arguments"].get("card_id")
            verified_facts["last_block_reason"] = proposal["arguments"].get("reason", "lost card")
            verified_facts["is_temporary_freeze"] = any(
                term in verified_facts['last_block_reason'].lower() for term in ('freeze', 'frozen'))
            verified_facts["has_blocked_card"] = True
        if isinstance(exec_data, dict):
            verified_facts['blocked_card_id'] = exec_data.get('card_id') or exec_data.get('id') or verified_facts.get('blocked_card_id')
            verified_facts['last_block_reason'] = exec_data.get('last_block_reason') or exec_data.get('reason') or verified_facts.get('last_block_reason', 'lost card')
            verified_facts['is_temporary_freeze'] = any(
                term in verified_facts['last_block_reason'].lower() for term in ('freeze', 'frozen'))
    elif tool_name == "unblock_card":
        last_event = "card_unblocked_success"
        data = exec_data if isinstance(exec_data, dict) else {}
        verified_facts['last_unblocked_card_id'] = (data.get('card_id') or data.get('id')
            or (proposal or {}).get('arguments', {}).get('card_id'))
        verified_facts["has_blocked_card"] = False
        verified_facts["blocked_cards"] = []
        verified_facts["temporary_freeze"] = False
        verified_facts["is_temporary_freeze"] = False
    elif tool_name in ("issue_card", "create_card"):
        last_event = "card_issued_success"
    elif tool_name == "replace_card":
        last_event = "card_replaced_success"

    return {
        "can_confirm": False,
        "proposed_tool": None,
        "action_plan": action_plan,
        "handoff_history": list(state.get("handoff_history", [])) + [result],
        "active_agent": active_agent,
        "next_step": "supervisor",
        "last_domain_event": last_event,
        "verified_domain_facts": verified_facts,
    }


def create_banking_workflow(
    supervisor: SupervisorAgent,
    cards_agent: Any,
    fincrime_agent: Any,
    checkpointer: Any | None = None,
    policy: dict | None = None,
):
    """Build and compile the multi-agent LangGraph StateGraph."""
    cards_node = CardsSpecialistNode(cards_agent, policy=policy)
    fincrime_node = FinCrimeSpecialistNode(fincrime_agent, policy=policy)

    builder = StateGraph(BankingWorkflowState)

    # Add agent and checkpoint nodes
    builder.add_node("supervisor", supervisor.plan_or_route)
    builder.add_node("cards_agent", cards_node.execute)
    builder.add_node("fincrime_agent", fincrime_node.execute)
    builder.add_node("human_confirmation", human_confirmation_node)

    # Set entry point
    builder.set_entry_point("supervisor")

    # Conditional routing from supervisor
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor_next,
        {
            "cards_agent": "cards_agent",
            "fincrime_agent": "fincrime_agent",
            "human_confirmation": "human_confirmation",
            END: END,
        },
    )

    # Specialist handoffs return to supervisor for review/compound next steps
    builder.add_edge("cards_agent", "supervisor")
    builder.add_edge("fincrime_agent", "supervisor")
    builder.add_edge("human_confirmation", "supervisor")

    memory = checkpointer or DurableSqliteSaver()
    return builder.compile(checkpointer=memory)
