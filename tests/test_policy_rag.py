"""Unit and integration tests for Policy Knowledge Base, local embeddings, and RAG retrieval."""
import json
from pathlib import Path
import numpy as np
import pytest

from card_chat.policy.embeddings import DeterministicLocalEmbedder
from card_chat.policy.models import PolicyChunk, PolicyDocument
from card_chat.policy.store import PolicyStore, parse_markdown_policy


def test_markdown_policy_parsing():
    """Verify versioned Markdown policy parsing with YAML frontmatter and section chunks."""
    doc, chunks = parse_markdown_policy("policies/POL-001-additional-card-issuance.md")

    assert doc.policy_id == "POL-001"
    assert doc.version == "1.0.0"
    assert doc.effective_date == "2026-01-01"
    assert "issue_card" in doc.applicable_intents
    assert "card_issuance_requested" in doc.triggering_events
    assert doc.conditions.get("has_blocked_card") is True
    assert "evaluate_unblock_eligibility" in doc.required_actions
    assert "prohibit_issuance_if_any_card_blocked" in doc.restrictions

    # Chunks are created per ## section
    assert len(chunks) >= 3
    titles = [c[0] for c in chunks]
    assert any("Scope and Objective" in t for t in titles)
    assert any("Core Restrictions" in t for t in titles)
    assert any("Customer Remediation" in t for t in titles)


def test_deterministic_local_embedder():
    """Verify local semantic embedding generator produces reproducible, normalized vectors."""
    embedder = DeterministicLocalEmbedder(dimension=256)

    vec1 = embedder.embed("unblock frozen card")
    vec2 = embedder.embed("unblock frozen card")
    vec3 = embedder.embed("temporary freeze of card")
    vec_unrelated = embedder.embed("apple pie baking recipe")

    assert vec1.shape == (256,)
    assert vec1.dtype == np.float32

    # Determinism: same input produces identical vector
    assert np.allclose(vec1, vec2)

    # L2 Normalization: norm must be 1.0
    norm1 = np.linalg.norm(vec1)
    assert abs(norm1 - 1.0) < 1e-5

    # Semantic relevance: related domain terms have significantly higher cosine similarity
    sim_related = embedder.similarity("unblock frozen card", "temporary freeze of card")
    sim_unrelated = embedder.similarity("unblock frozen card", "apple pie baking recipe")

    assert sim_related > 0.45
    assert sim_unrelated < 0.1
    assert sim_related > sim_unrelated


def test_policy_store_indexing_and_mandatory_retrieval(tmp_path):
    """Verify indexing directory and deterministic retrieval by policy ID, intent, and event."""
    db_file = str(tmp_path / "test_policies.db")
    store = PolicyStore(db_file)

    res = store.index_directory("policies")
    assert res["total"] >= 4
    assert res["indexed"] >= 4

    # 1. Deterministic mandatory retrieval by exact policy_id
    pol001_chunks = store.retrieve_mandatory(policy_id="POL-001")
    assert len(pol001_chunks) > 0
    assert all(c.policy_id == "POL-001" for c in pol001_chunks)
    assert pol001_chunks[0].version == "1.0.0"
    assert "policies/POL-001" in pol001_chunks[0].source

    # 2. Deterministic mandatory retrieval by applicable intent
    issue_chunks = store.retrieve_mandatory(intent="issue_card")
    policy_ids = {c.policy_id for c in issue_chunks}
    assert "POL-001" in policy_ids
    assert "POL-004" in policy_ids  # POL-004 applies to all state-changing actions

    # 3. Deterministic mandatory retrieval by triggering event
    post_block_chunks = store.retrieve_mandatory(event_type="card_blocked_success")
    post_block_pids = {c.policy_id for c in post_block_chunks}
    assert "POL-003" in post_block_pids

    # 4. Inactive future policy filtering by effective_date
    future_filtered = store.retrieve_mandatory(policy_id="POL-001", effective_date_filter="2025-01-01")
    assert len(future_filtered) == 0  # POL-001 effective date is 2026-01-01


def test_semantic_vector_search_and_attribution(tmp_path):
    """Verify semantic vector search ranks relevant chunks and includes attribution metadata."""
    store = PolicyStore(str(tmp_path / "policies.db"))
    store.index_directory("policies")

    # Search for stolen card replacement guidance
    results = store.search("my card was stolen, can I get a replacement?", top_k=3)
    assert len(results) > 0

    top_result = results[0]
    assert top_result.policy_id in ("POL-002", "POL-003")
    assert top_result.score > 0.2
    assert top_result.source.endswith(".md")
    assert top_result.version == "1.0.0"
    assert "title" in top_result.metadata


def test_hybrid_retrieval_deduplication(tmp_path):
    """Verify hybrid retrieval combines mandatory compliance chunks and semantic matches without duplicates."""
    store = PolicyStore(str(tmp_path / "policies.db"))
    store.index_directory("policies")

    retrieved = store.retrieve(
        query="Issue another card on my account",
        intent="issue_card",
        event_type="card_issuance_requested",
        top_k=5,
    )

    chunk_ids = [c.chunk_id for c in retrieved]
    # No duplicate chunk IDs
    assert len(chunk_ids) == len(set(chunk_ids))

    policy_ids = {c.policy_id for c in retrieved}
    assert "POL-001" in policy_ids


def test_policy_reindexing_and_stale_embedding_purge(tmp_path):
    """Verify modifying a policy updates the index and removes stale chunks/embeddings."""
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()

    doc_file = policy_dir / "POL-999-test-policy.md"
    doc_file.write_text(
        "---\npolicy_id: POL-999\nversion: 1.0.0\neffective_date: '2026-01-01'\n"
        "title: Original Policy\napplicable_intents:\n  - test_intent\n---\n"
        "# POL-999: Original\n\n## Section A\nOriginal text content.\n",
        encoding="utf-8",
    )

    store = PolicyStore(str(tmp_path / "policies.db"))
    res1 = store.index_directory(policy_dir)
    assert res1["indexed"] == 1

    doc1 = store.get_document("POL-999")
    assert doc1.version == "1.0.0"
    assert doc1.title == "Original Policy"

    # Modify policy file (simulate policy update)
    doc_file.write_text(
        "---\npolicy_id: POL-999\nversion: 1.1.0\neffective_date: '2026-01-01'\n"
        "title: Updated Policy\napplicable_intents:\n  - test_intent\n---\n"
        "# POL-999: Updated\n\n## Section A\nUpdated text content with fresh terms.\n\n## Section B\nNew section.\n",
        encoding="utf-8",
    )

    res2 = store.index_directory(policy_dir)
    assert res2["indexed"] == 1  # Updated

    doc2 = store.get_document("POL-999")
    assert doc2.version == "1.1.0"
    assert doc2.title == "Updated Policy"

    # Check chunks: old chunks purged, 3 new chunks (intro + Section A + Section B)
    chunks = store.retrieve_mandatory(policy_id="POL-999")
    assert len(chunks) == 3
    assert all(c.version == "1.1.0" for c in chunks)

    # Remove policy file (simulate policy deletion)
    doc_file.unlink()
    res3 = store.index_directory(policy_dir)
    assert res3["deleted"] == 1
    assert store.get_document("POL-999") is None
    assert len(store.retrieve_mandatory(policy_id="POL-999")) == 0
