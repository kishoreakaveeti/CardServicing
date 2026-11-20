"""Data models and contracts for policy-driven banking orchestration and RAG."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class PolicyDocument(BaseModel):
    """Metadata and content for a versioned company banking policy document."""
    policy_id: str = Field(description="Unique policy identifier (e.g. POL-001)")
    version: str = Field(description="Semantic version string (e.g. 1.0.0)")
    effective_date: str = Field(description="ISO date string when policy took effect (YYYY-MM-DD)")
    title: str = Field(description="Human-readable title of the policy")
    status: str = Field(default="approved", description="Workflow approval status (approved, draft, deprecated)")
    approved: bool = Field(default=True, description="Whether policy is signed off for automated enforcement")
    applicable_intents: list[str] = Field(default_factory=list, description="Intents governed by this policy")
    triggering_events: list[str] = Field(default_factory=list, description="Domain events triggering this policy")
    conditions: dict[str, Any] = Field(default_factory=dict, description="Structured criteria for applicability")
    required_actions: list[str] = Field(default_factory=list, description="Mandatory actions required by policy")
    restrictions: list[str] = Field(default_factory=list, description="Explicit prohibitions and constraints")
    source: str = Field(description="Path to original Markdown file")
    raw_content: str = Field(default="", description="Full raw markdown content")
    content_hash: str = Field(default="", description="SHA-256 hash of document source")


class PolicyChunk(BaseModel):
    """A searchable chunk of a policy document with vector and metadata attributes."""
    chunk_id: str = Field(description="Unique chunk identifier (e.g. POL-001#chunk-0)")
    policy_id: str = Field(description="Parent policy ID")
    version: str = Field(description="Policy version at index time")
    chunk_index: int = Field(default=0, description="Sequential index within the document")
    title: str = Field(description="Section heading or policy title")
    content: str = Field(description="Text content of the chunk")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata inherited from document")
    source: str = Field(default="", description="Source file path")
    score: float = Field(default=0.0, description="Similarity score during search")


class SearchResult(BaseModel):
    """Raw vector/lexical search hit from a VectorStore."""
    chunk: PolicyChunk
    score: float = 0.0


class PolicyEvidence(BaseModel):
    """Grounded policy passage returned by PolicyRetriever with provenance."""
    policy_id: str
    version: str
    title: str
    section_title: str
    content: str
    source: str
    content_hash: str = ''
    score: float = 0.0
    mandatory: bool = False
    effective_date: str = ""
    restrictions: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)


class PendingCustomerQuestion(BaseModel):
    """A policy-driven interactive question requiring customer response before next action."""
    question_type: Literal["unblock_offer", "replacement_offer", "clarification"]
    question_text: str
    target_card_id: str | None = None
    target_account_number: str | None = None
    policy_id: str
    policy_version: str
    expected_responses: list[str] = Field(default_factory=lambda: ["yes", "no"])
    yes_intent: str | None = None  # Intent to execute if customer accepts
    no_intent: str | None = None   # Intent to execute if customer declines
    subsequent_intent: str | None = None  # Original pending intent to resume after acceptance


class PolicyEvaluationResult(BaseModel):
    """Structured result of evaluating retrieved policies against verified domain facts."""
    permitted: bool = True
    clarification_required: bool = False
    requires_human_approval: bool = False
    geographic_decision: dict[str, Any] | None = None
    prohibited: bool = False
    decision_reason: str = ""
    retrieved_policies: list[dict[str, Any]] = Field(default_factory=list)
    pinned_versions: dict[str, str] = Field(default_factory=dict)
    required_actions: list[str] = Field(default_factory=list)
    restrictions_enforced: list[str] = Field(default_factory=list)
    pending_question: PendingCustomerQuestion | None = None
    customer_decision: str | None = None
    recommended_delegation: str | None = None  # 'cards_agent' | 'fincrime_agent' | 'human_confirmation' | 'respond'
    recommended_intent: str | None = None
    recommended_context: dict[str, Any] = Field(default_factory=dict)
    planned_intents: list[str] = Field(default_factory=list)
    policy_error: str | None = None
    evidence_citations: list[dict[str, Any]] = Field(default_factory=list)
    recommendation: dict[str, Any] | None = None
