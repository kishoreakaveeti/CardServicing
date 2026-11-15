"""LangGraph workflow state schema for privacy-preserving multi-agent banking."""
import operator
from typing import Annotated, Any, TypedDict
from card_chat.orchestration.contracts import AgentHandoff, HandoffResult


class BankingWorkflowState(TypedDict, total=False):
    """Shared workflow state managed across LangGraph supervisor and specialist nodes."""

    # Conversation and identity scope
    owner: str
    session_id: str
    workflow_id: str

    # Sanitized messages and draft facts
    messages: list[dict[str, Any]]
    verified_draft: dict[str, Any]
    allowed_tools: dict[str, Any]

    # Active delegation and minimal-context contracts
    current_handoff: AgentHandoff | None
    handoff_history: list[HandoffResult]
    active_agent: str | None
    next_step: str | None  # 'cards_agent' | 'fincrime_agent' | 'supervisor' | 'human_confirmation' | 'respond'

    # Compound request tracking
    planned_intents: list[str]
    completed_intents: list[str]

    # Tool proposal and human confirmation lifecycle
    proposed_tool: dict[str, Any] | None
    review: str | None
    can_confirm: bool
    confirmation_label: str | None
    input_tool: str | None

    # Reasoning loop control
    iteration_count: int
    final_response: str | None
    error: str | None
