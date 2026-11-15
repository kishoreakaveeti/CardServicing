"""Structured minimal-context handoff contracts between Supervisor and Specialist Agents."""
from dataclasses import dataclass, field
from typing import Any, Literal
from pydantic import BaseModel, Field


class AgentHandoff(BaseModel):
    """Explicit, structured minimal-context contract for agent handoffs.

    Enforces that agents do NOT exchange entire unstructured conversation histories.
    """
    origin_agent: str = Field(description="Name of the delegating agent (e.g. 'supervisor', 'cards_agent')")
    target_agent: str = Field(description="Name of the specialist agent to invoke ('cards_agent', 'fincrime_agent')")
    intent: str = Field(description="Explicit customer banking intent (e.g. 'block_card', 'issue_card', 'view_accounts')")
    relevant_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Minimal sanitized facts necessary for the specialist (e.g. {'card_id': 'selected_input_...'}).",
    )
    session_id: str = Field(description="Scoped customer session/conversation ID")
    workflow_id: str = Field(default="default", description="Scoped multi-step workflow ID")
    constraints: list[str] = Field(
        default_factory=list,
        description="Banking constraints and safety rules (e.g. ['check_blocked', 'require_confirmation'])",
    )


class HandoffResult(BaseModel):
    """Result returned by a specialist agent back to the supervisor or workflow."""
    source_agent: str = Field(description="Name of the specialist reporting the result")
    status: Literal["success", "failed", "requires_confirmation", "clarification_needed"] = Field(
        description="Outcome of the specialist's reasoning turn"
    )
    message: str = Field(description="Explanation or customer message from the specialist")
    tool_proposal: dict[str, Any] | None = Field(
        default=None,
        description="Proposed MCP tool call name and arguments if an action was selected",
    )
    data: dict[str, Any] | None = Field(
        default=None,
        description="Structured domain data returned by tool execution if completed",
    )
    remaining_intents: list[str] = Field(
        default_factory=list,
        description="Any compound sub-intents that still need to be addressed in the workflow",
    )
