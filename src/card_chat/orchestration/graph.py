"""LangGraph StateGraph multi-agent orchestration workflow with checkpointing."""
import logging
from typing import Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

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
    return {"can_confirm": True}


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
    builder.add_edge("human_confirmation", END)

    memory = checkpointer or MemorySaver()
    return builder.compile(checkpointer=memory)
