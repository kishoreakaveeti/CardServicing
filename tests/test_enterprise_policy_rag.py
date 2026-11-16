"""Tests for Enterprise Policy-Driven RAG: Interfaces, Adapters, and Ingestion Pipeline."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from card_chat.policy import (
    DocumentSource,
    EmbeddingProvider,
    VectorStore,
    PolicyRetriever,
    PolicyEvaluator,
    PolicyDocument,
    PolicyChunk,
    PolicyEvidence,
    SearchResult,
    FileSystemDocumentSource,
    InMemoryDocumentSource,
    FastEmbedEmbeddingProvider,
    DeterministicLocalEmbeddingProvider,
    QdrantVectorStore,
    InMemoryVectorStore,
    HybridPolicyRetriever,
    GroundedPolicyEvaluator,
    PolicyIngestionPipeline,
    build_document_source,
    build_embedding_provider,
    build_vector_store,
    build_policy_retriever,
    build_policy_evaluator,
    build_policy_pipeline,
)


# ==============================================================================
# 1. Architecture and Dependency Injection Contract Tests
# ==============================================================================

def test_ports_and_adapters_protocols():
    """Verify that adapters conform to their respective runtime-checkable Protocols."""
    # Document sources
    assert isinstance(FileSystemDocumentSource("policies"), DocumentSource)
    assert isinstance(InMemoryDocumentSource(), DocumentSource)

    # Embedding providers
    assert isinstance(FastEmbedEmbeddingProvider(), EmbeddingProvider)
    assert isinstance(DeterministicLocalEmbeddingProvider(), EmbeddingProvider)

    # Vector stores
    assert isinstance(QdrantVectorStore(), VectorStore)
    assert isinstance(InMemoryVectorStore(), VectorStore)

    # Retriever and evaluator
    source = InMemoryDocumentSource()
    embedder = DeterministicLocalEmbeddingProvider()
    store = InMemoryVectorStore()
    retriever = HybridPolicyRetriever(source, embedder, store)
    evaluator = GroundedPolicyEvaluator(retriever)

    assert isinstance(retriever, PolicyRetriever)
    assert isinstance(evaluator, PolicyEvaluator)


def test_composition_root_factory_injection(tmp_path):
    """Verify composition root constructs adapters based on configuration."""
    config = {
        "POLICIES_DIR": str(tmp_path),
        "EMBEDDING_PROVIDER": "deterministic",
        "POLICY_VECTOR_STORE": "memory",
    }
    retriever = build_policy_retriever(config=config)
    evaluator = build_policy_evaluator(retriever=retriever, config=config)

    assert isinstance(retriever, HybridPolicyRetriever)
    assert isinstance(evaluator, GroundedPolicyEvaluator)


# ==============================================================================
# 2. Embedding Providers (FastEmbed & Deterministic)
# ==============================================================================

def test_deterministic_embedding_provider():
    """Verify deterministic local embedder produces normalized vectors."""
    provider = DeterministicLocalEmbeddingProvider(dimension=256)
    assert provider.dimension == 256
    assert "deterministic" in provider.model_name

    vec = provider.embed_query("Card servicing freeze policy")
    assert len(vec) == 256
    # Verify unit length
    norm = sum(x * x for x in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-4

    docs = provider.embed_documents(["Document 1", "Document 2"])
    assert len(docs) == 2
    assert len(docs[0]) == 256


def test_fastembed_embedding_provider():
    """Verify local FastEmbed ONNX embedding provider."""
    provider = FastEmbedEmbeddingProvider(model_name="BAAI/bge-small-en-v1.5")
    assert provider.dimension == 384
    assert "bge-small" in provider.model_name

    query_vec = provider.embed_query("lost card replacement policy")
    assert len(query_vec) == 384
    norm = sum(x * x for x in query_vec) ** 0.5
    assert abs(norm - 1.0) < 1e-3

    doc_vecs = provider.embed_documents(["Temporary freeze", "Permanent block"])
    assert len(doc_vecs) == 2
    assert len(doc_vecs[0]) == 384


# ==============================================================================
# 3. Vector Stores (Qdrant & In-Memory)
# ==============================================================================

def test_qdrant_vector_store_in_memory():
    """Verify QdrantVectorStore in-memory indexing, search, filtering, and deletion."""
    store = QdrantVectorStore(client=None, url=None, path=None, collection_name="test_banking_policies")
    store.initialize(dimension=256)
    assert store.count() == 0

    chunk1 = PolicyChunk(
        chunk_id="POL-001#c0",
        policy_id="POL-001",
        version="1.0.0",
        chunk_index=0,
        title="Issuance Restriction",
        content="Prohibit issuance if account has a blocked card.",
        metadata={"applicable_intents": ["issue_card"], "status": "approved"},
    )
    chunk2 = PolicyChunk(
        chunk_id="POL-002#c0",
        policy_id="POL-002",
        version="1.0.0",
        chunk_index=0,
        title="Replacement Rules",
        content="Permit replacement for compromised cards.",
        metadata={"applicable_intents": ["replace_card"], "status": "approved"},
    )

    vec1 = [0.1] * 256
    vec2 = [-0.1] * 256
    store.upsert_chunks([chunk1, chunk2], [vec1, vec2])
    assert store.count() == 2

    # Vector search matching vec1
    hits = store.search(vec1, limit=1)
    assert len(hits) == 1
    assert hits[0].chunk.policy_id == "POL-001"

    # Search with metadata filter
    filtered_hits = store.search(vec1, limit=5, filter_metadata={"policy_id": "POL-002"})
    assert len(filtered_hits) == 1
    assert filtered_hits[0].chunk.policy_id == "POL-002"

    # Delete policy_id
    store.delete_by_policy_id("POL-001")
    assert store.count() == 1
    remaining = store.search(vec1, limit=5)
    assert len(remaining) == 1
    assert remaining[0].chunk.policy_id == "POL-002"


# ==============================================================================
# 4. Document Source and Policy Validation
# ==============================================================================

def test_filesystem_document_source_parsing():
    """Verify FileSystemDocumentSource reads and validates policies from policies/."""
    source = FileSystemDocumentSource("policies")
    docs = source.list_documents(approved_only=True)
    assert len(docs) >= 4

    pids = {d.policy_id for d in docs}
    assert {"POL-001", "POL-002", "POL-003", "POL-004"}.issubset(pids)

    pol1 = source.get_document("POL-001")
    assert pol1 is not None
    assert pol1.policy_id == "POL-001"
    assert pol1.version == "1.0.0"
    assert pol1.approved is True
    assert "issue_card" in pol1.applicable_intents


def test_document_source_rejects_unapproved(tmp_path):
    """Verify document source excludes unapproved drafts when approved_only is True."""
    draft_file = tmp_path / "POL-999-draft.md"
    draft_file.write_text(
        "---\npolicy_id: POL-999\nversion: 0.1.0\neffective_date: '2026-01-01'\n"
        "title: Draft Policy\nstatus: draft\napproved: false\n---\n# Content\nDraft rules",
        encoding="utf-8",
    )
    source = FileSystemDocumentSource(tmp_path)
    assert len(source.list_documents(approved_only=True)) == 0
    assert len(source.list_documents(approved_only=False)) == 1


# ==============================================================================
# 5. Independent Ingestion Pipeline and Audit Trail
# ==============================================================================

def test_ingestion_pipeline_atomic_and_audit(tmp_path):
    """Verify pipeline validates, chunks, embeds, indexes into Qdrant, and logs audit trail."""
    audit_file = tmp_path / "audit.json"
    source = FileSystemDocumentSource("policies")
    embedder = DeterministicLocalEmbeddingProvider(dimension=256)
    store = QdrantVectorStore(collection_name="test_audit_policies")
    pipeline = PolicyIngestionPipeline(
        document_source=source,
        embedding_provider=embedder,
        vector_store=store,
        audit_log_path=audit_file,
    )

    results = pipeline.ingest_all()
    assert len(results) >= 4
    for pid, count in results.items():
        assert count > 0

    assert store.count() > 0

    # Verify audit log was created
    audit_data = pipeline.get_audit_trail()
    assert len(audit_data) >= 4
    entry = audit_data[0]
    assert "policy_id" in entry
    assert "version" in entry
    assert "timestamp" in entry
    assert "content_hash" in entry

    # Inspect status
    info = pipeline.inspect()
    assert info["total_vectors"] == store.count()
    assert info["embedding_dimension"] == 256
    assert len(info["available_documents"]) >= 4


# ==============================================================================
# 6. Hybrid Policy Retriever
# ==============================================================================

def test_hybrid_policy_retriever():
    """Verify HybridPolicyRetriever mandatory retrieval + vector search."""
    source = FileSystemDocumentSource("policies")
    embedder = DeterministicLocalEmbeddingProvider(dimension=256)
    store = QdrantVectorStore(collection_name="test_retriever_policies")
    pipeline = PolicyIngestionPipeline(source, embedder, store)
    pipeline.ingest_all()

    retriever = HybridPolicyRetriever(source, embedder, store)

    # 1. Deterministic mandatory retrieval for intent issue_card
    mandatory = retriever.retrieve_mandatory(intent="issue_card")
    pids = {m.policy_id for m in mandatory}
    assert "POL-001" in pids
    assert "POL-004" in pids  # Governance baseline is always mandatory

    # 2. Hybrid retrieve for blocked account facts
    evidence = retriever.retrieve(
        intent="issue_card",
        facts={"has_blocked_card": True, "is_temporary_freeze": True},
        query="I need another card",
    )
    assert len(evidence) >= 2
    ev_pids = {e.policy_id for e in evidence}
    assert "POL-001" in ev_pids
    assert "POL-004" in ev_pids

    # Check evidence metadata attribution
    pol1_ev = next(e for e in evidence if e.policy_id == "POL-001")
    assert pol1_ev.version == "1.0.0"
    assert "policies/POL-001" in pol1_ev.source
    assert pol1_ev.effective_date == "2026-01-01"
