"""Enterprise Ports for Policy-Driven RAG Architecture.

Defines decoupled interfaces for document sourcing, embedding generation,
vector indexing, policy retrieval, and policy decision evaluation.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import (
    PolicyChunk,
    PolicyDocument,
    PolicyEvidence,
    PolicyEvaluationResult,
    SearchResult,
)


@runtime_checkable
class DocumentSource(Protocol):
    """Retrieves approved business policy documents and version metadata."""

    def list_documents(self, approved_only: bool = True) -> list[PolicyDocument]:
        """List all available policy documents."""
        ...

    def get_document(self, policy_id: str, version: str | None = None) -> PolicyDocument | None:
        """Get a specific policy document by ID and optional version."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Generates document and query embeddings."""

    @property
    def dimension(self) -> int:
        """Vector dimensionality produced by this model."""
        ...

    @property
    def model_name(self) -> str:
        """Identifier of the active embedding model."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate normalized embedding vectors for a batch of documents."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Generate a normalized embedding vector for a single query."""
        ...


@runtime_checkable
class VectorStore(Protocol):
    """Indexes, searches, and manages vector embeddings with associated metadata."""

    def initialize(self, dimension: int) -> None:
        """Initialize collection and index structures with expected dimensions."""
        ...

    def upsert_chunks(self, chunks: list[PolicyChunk], embeddings: list[list[float]]) -> None:
        """Insert or replace policy chunks and their embedding vectors."""
        ...

    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        score_threshold: float = 0.0,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Search for most similar chunks matching query vector and metadata filter."""
        ...

    def delete_by_policy_id(self, policy_id: str) -> int:
        """Delete all chunks associated with a specific policy ID."""
        ...

    def count(self) -> int:
        """Return total number of indexed vector chunks."""
        ...


@runtime_checkable
class PolicyRetriever(Protocol):
    """Retrieves grounded policy evidence using deterministic and semantic strategies."""

    def retrieve(
        self,
        intent: str,
        facts: dict[str, Any],
        query: str | None = None,
        limit: int = 5,
    ) -> list[PolicyEvidence]:
        """Retrieve applicable policy evidence for an intent and verified facts."""
        ...

    def retrieve_mandatory(
        self,
        policy_id: str | None = None,
        intent: str | None = None,
        event: str | None = None,
    ) -> list[PolicyEvidence]:
        """Deterministically retrieve mandatory policies that must not be missed."""
        ...


@runtime_checkable
class PolicyEvaluator(Protocol):
    """Converts applicable policies and verified banking facts into structured decisions."""

    def evaluate(
        self,
        intent: str | None,
        user_text: str = "",
        facts: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        pending_question: Any | None = None,
        last_domain_event: str | None = None,
        pinned_policies: dict[str, str] | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluate policy rules against verified facts and conversation state."""
        ...

    def validate_execution(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        facts: dict[str, Any],
        state: dict[str, Any] | None = None,
    ) -> PolicyEvaluationResult:
        """Perform zero-trust pre-execution validation before state mutation."""
        ...
