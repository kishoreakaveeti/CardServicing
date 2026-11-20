"""LangGraph workflow state schema for privacy-preserving, policy-driven multi-agent banking."""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict
from card_chat.orchestration.contracts import AgentHandoff, HandoffResult


class BankingWorkflowState(TypedDict, total=False):
    """Shared workflow state managed across LangGraph supervisor, policy, and specialist nodes."""

    # Conversation and identity scope
    owner: str
    session_id: str
    workflow_id: str

    # Sanitized messages and draft facts
    messages: list[dict[str, Any]]
    verified_draft: dict[str, Any]
    authorized_arguments: dict[str, Any]
    allowed_tools: dict[str, Any]

    # Customer intent and verified domain facts
    customer_intent: str | None
    verified_domain_facts: dict[str, Any]
    last_domain_event: str | None

    # Policy RAG retrieval and structured evaluation
    retrieved_policies: list[dict[str, Any]]
    pinned_policies: dict[str, str]
    policy_evaluation: dict[str, Any] | None
    policy_error: str | None
    policy_planning: dict[str, Any] | None
    country_context: dict[str, Any]
    geographic_binding: dict[str, Any] | None
    geographic_decision: dict[str, Any] | None
    geographic_notice: str | None
    geographic_pending_intents: list[str]

    # Interactive customer questions and expected responses
    pending_customer_question: dict[str, Any] | None
    customer_decision: str | None
    next_specialist: str | None

    # Active delegation and minimal-context contracts
    current_handoff: AgentHandoff | None
    handoff_history: list[HandoffResult]
    active_agent: str | None
    next_step: str | None  # 'cards_agent' | 'fincrime_agent' | 'policy_evaluator' | 'supervisor' | 'human_confirmation' | 'respond'

    # Compound request tracking
    planned_intents: list[str]
    completed_intents: list[str]
    action_plan: list[dict[str, Any]]
    execution_outcome: dict[str, Any] | None

    # Tool proposal and human confirmation lifecycle
    proposed_tool: dict[str, Any] | None
    review: str | None
    can_confirm: bool
    confirmation_label: str | None
    input_tool: str | None

    # FinCrime human-in-the-loop approval lifecycle
    fincrime_case_id: str | None
    fincrime_approved: bool | None
    fincrime_approval_reason: str | None
    pending_fincrime_approval: bool | None

    # Reasoning loop control
    iteration_count: int
    final_response: str | None
    error: str | None
