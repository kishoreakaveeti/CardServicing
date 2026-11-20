"""Composition root and dependency injection factory for policy RAG capability."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .adapters.document_source import FileSystemDocumentSource, InMemoryDocumentSource, ReleaseDocumentSource
from .adapters.embeddings.deterministic_provider import DeterministicLocalEmbeddingProvider
from .adapters.embeddings.fastembed_provider import FastEmbedEmbeddingProvider
from .adapters.evaluator import GroundedPolicyEvaluator
from .adapters.retriever import HybridPolicyRetriever
from .adapters.vector_stores.memory_store import InMemoryVectorStore
from .adapters.vector_stores.qdrant_store import QdrantVectorStore
from .pipeline.ingestion import PolicyIngestionPipeline
from .ports import DocumentSource, EmbeddingProvider, PolicyEvaluator, PolicyRetriever, VectorStore
from .release_store import PolicyReleaseStore, ReleaseRulesSource

logger = logging.getLogger(__name__)


def build_document_source(
    source_type: str = "filesystem",
    policies_dir: str | Path = "policies",
) -> DocumentSource:
    if source_type == "filesystem":
        return FileSystemDocumentSource(directory=policies_dir)
    elif source_type == "memory":
        return InMemoryDocumentSource()
    raise ValueError(f"Unknown document source type: {source_type}")


def build_embedding_provider(
    provider_type: str | None = None,
    model_name: str | None = None,
) -> EmbeddingProvider:
    provider = (provider_type or os.getenv("EMBEDDING_PROVIDER", "fastembed")).lower()
    if provider == "fastembed":
        m_name = model_name or os.getenv("FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
        return FastEmbedEmbeddingProvider(model_name=m_name)
    elif provider in ("deterministic", "local", "stub"):
        return DeterministicLocalEmbeddingProvider()
    raise ValueError(f"Unknown embedding provider: {provider}")


def build_vector_store(
    store_type: str | None = None,
    qdrant_url: str | None = None,
    collection_name: str = "banking_policies",
) -> VectorStore:
    store = (store_type or os.getenv("POLICY_VECTOR_STORE", "qdrant")).lower()
    if store == "qdrant":
        url = qdrant_url or os.getenv("QDRANT_URL")
        if not url:
            raise ValueError("QDRANT_URL is required; use :memory: explicitly for tests")
        if url in (":memory:", "memory"):
            return QdrantVectorStore(client=None, url=None, path=None, collection_name=collection_name)
        elif url.startswith("http://") or url.startswith("https://"):
            return QdrantVectorStore(url=url, collection_name=collection_name)
        else:
            return QdrantVectorStore(path=url, collection_name=collection_name)
    elif store == "memory":
        return InMemoryVectorStore()
    raise ValueError(f"Unknown vector store type: {store}")


def build_policy_retriever(
    document_source: DocumentSource | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    config: dict[str, Any] | None = None,
    release_store: PolicyReleaseStore | None = None,
) -> PolicyRetriever:
    config = config or {}
    policies_dir = config.get("POLICIES_DIR", os.getenv("POLICIES_DIR", "policies"))
    src = document_source or build_document_source(policies_dir=policies_dir)
    r_store = release_store or config.get("RELEASE_STORE")
    if r_store is not None and not isinstance(src, ReleaseDocumentSource):
        src = ReleaseDocumentSource(src, r_store)
    emb = embedding_provider or build_embedding_provider(
        provider_type=config.get("EMBEDDING_PROVIDER"),
        model_name=config.get("FASTEMBED_MODEL"),
    )
    store = vector_store or build_vector_store(
        store_type=config.get("POLICY_VECTOR_STORE"),
        qdrant_url=config.get("QDRANT_URL"),
        collection_name=config.get("POLICY_COLLECTION", os.getenv("POLICY_COLLECTION", "banking_policies")),
    )
    return HybridPolicyRetriever(
        document_source=src,
        embedding_provider=emb,
        vector_store=store,
    )


def build_policy_evaluator(
    retriever: PolicyRetriever | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> PolicyEvaluator:
    if isinstance(retriever, dict) and config is None:
        config = retriever
        retriever = kwargs.get("retriever")
    r = retriever or kwargs.get("retriever") or build_policy_retriever(config=config)
    from .geography import FileRulesSource
    rules = kwargs.get('geographic_rules')
    if rules is None:
        r_store = kwargs.get('release_store') or (config or {}).get("RELEASE_STORE")
        if r_store is not None:
            rules = ReleaseRulesSource(r_store, (config or {}).get('POLICIES_DIR'))
        else:
            rules = FileRulesSource((config or {}).get('POLICIES_DIR'))
    return GroundedPolicyEvaluator(retriever=r, geographic_rules=rules)


def build_policy_pipeline(
    policies_dir: str | Path = "policies",
    vector_store_type: str | None = None,
    qdrant_url: str | None = None,
    embedding_provider_type: str | None = None,
    config: dict[str, Any] | None = None,
) -> PolicyIngestionPipeline:
    config = config or {}
    src = build_document_source(policies_dir=policies_dir)
    emb = build_embedding_provider(
        provider_type=embedding_provider_type or config.get("EMBEDDING_PROVIDER"),
        model_name=config.get("FASTEMBED_MODEL")
    )
    store = build_vector_store(
        store_type=vector_store_type or config.get("POLICY_VECTOR_STORE"),
        qdrant_url=qdrant_url or config.get("QDRANT_URL"),
        collection_name=config.get("POLICY_COLLECTION", os.getenv("POLICY_COLLECTION", "banking_policies")),
    )
    return PolicyIngestionPipeline(
        document_source=src,
        embedding_provider=emb,
        vector_store=store,
        audit_log_path=config.get('POLICY_AUDIT_LOG', os.getenv('POLICY_AUDIT_LOG', 'data/policy_audit.json')),
    )


def build_runtime_policy_services(config: dict[str, Any], *, retriever=None):
    """Compose live services; readiness is read-only and ingestion remains independent.

    Supplying a retriever is the explicit dependency-injection seam for tests.
    Production configuration always requires remote Qdrant and FastEmbed.
    """
    if retriever is None:
        cfg = {**os.environ, **config}
        if cfg.get("EMBEDDING_PROVIDER", "fastembed") != "fastembed":
            raise RuntimeError("Live policy runtime requires EMBEDDING_PROVIDER=fastembed")
        if cfg.get("POLICY_VECTOR_STORE", "qdrant") != "qdrant" or not str(cfg.get("QDRANT_URL", "")).startswith(("http://", "https://")):
            raise RuntimeError("Live policy runtime requires a persistent QDRANT_URL (http/https)")
        retriever = build_policy_retriever(config=cfg)
    if isinstance(retriever, HybridPolicyRetriever):
        retriever.strict_index = True
        try:
            retriever.check_ready()
        except Exception as exc:
            raise RuntimeError("Approved policy index unavailable; run the independent policy ingestion pipeline") from exc
    return retriever, build_policy_evaluator(retriever=retriever, config=config)
