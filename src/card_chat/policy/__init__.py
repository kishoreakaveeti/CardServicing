"""Enterprise policy-driven agentic orchestration with RAG and LangGraph state."""
from .models import (
    PendingCustomerQuestion,
    PolicyChunk,
    PolicyDocument,
    PolicyEvidence,
    PolicyEvaluationResult,
    SearchResult,
)
from .ports import (
    DocumentSource,
    EmbeddingProvider,
    PolicyEvaluator,
    PolicyRetriever,
    VectorStore,
)
from .adapters.document_source import FileSystemDocumentSource, InMemoryDocumentSource
from .adapters.embeddings.fastembed_provider import FastEmbedEmbeddingProvider
from .adapters.embeddings.deterministic_provider import DeterministicLocalEmbeddingProvider
from .adapters.vector_stores.qdrant_store import QdrantVectorStore
from .adapters.vector_stores.memory_store import InMemoryVectorStore
from .adapters.retriever import HybridPolicyRetriever
from .adapters.evaluator import GroundedPolicyEvaluator
from .pipeline.ingestion import PolicyIngestionPipeline
from .factory import (
    build_document_source,
    build_embedding_provider,
    build_policy_evaluator,
    build_policy_pipeline,
    build_policy_retriever,
    build_vector_store,
)

# Backwards compatibility aliases
DeterministicLocalEmbedder = DeterministicLocalEmbeddingProvider
PolicyStore = QdrantVectorStore

__all__ = [
    # Models
    "PolicyDocument",
    "PolicyChunk",
    "SearchResult",
    "PolicyEvidence",
    "PendingCustomerQuestion",
    "PolicyEvaluationResult",
    # Ports
    "DocumentSource",
    "EmbeddingProvider",
    "VectorStore",
    "PolicyRetriever",
    "PolicyEvaluator",
    # Adapters
    "FileSystemDocumentSource",
    "InMemoryDocumentSource",
    "FastEmbedEmbeddingProvider",
    "DeterministicLocalEmbeddingProvider",
    "QdrantVectorStore",
    "InMemoryVectorStore",
    "HybridPolicyRetriever",
    "GroundedPolicyEvaluator",
    # Pipeline
    "PolicyIngestionPipeline",
    # Factory
    "build_document_source",
    "build_embedding_provider",
    "build_vector_store",
    "build_policy_retriever",
    "build_policy_evaluator",
    "build_policy_pipeline",
    # Legacy
    "DeterministicLocalEmbedder",
    "PolicyStore",
]
