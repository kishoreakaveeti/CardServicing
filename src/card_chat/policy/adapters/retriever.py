"""Hybrid PolicyRetriever adapter combining deterministic mandatory lookup and semantic search."""
from __future__ import annotations

import logging
from typing import Any

from ..models import PolicyEvidence
from ..ports import DocumentSource, EmbeddingProvider, PolicyRetriever, VectorStore

logger = logging.getLogger(__name__)


class HybridPolicyRetriever(PolicyRetriever):
    """Retrieves policy evidence through hybrid deterministic and semantic mechanisms.

    Ensures that mandatory regulatory policies (such as authorization and blocking rules)
    can NEVER be missed because of vector similarity distance, while leveraging dense vector
    search to retrieve relevant contextual clauses and explanations.
    """

    def __init__(
        self,
        document_source: DocumentSource,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
    ):
        self._doc_source = document_source
        self._embedder = embedding_provider
        self._store = vector_store
        self.strict_index = False

    def _active_documents(self):
        documents = self._doc_source.list_documents(approved_only=True)
        active = {}
        for doc in documents:
            if not doc.approved or doc.status != "approved":
                continue
            if doc.policy_id in active:
                raise RuntimeError(f"Multiple approved versions for {doc.policy_id}; activate one version")
            active[doc.policy_id] = doc
        return list(active.values())

    def _verify_indexed(self, doc):
        hits = self._store.search(
            self._embedder.embed_query(doc.title), limit=1,
            filter_metadata={"policy_id": doc.policy_id, "version": doc.version,
                             "approved": True, "status": "approved"},
        )
        if not any(h.chunk.policy_id == doc.policy_id and h.chunk.version == doc.version
                   and h.chunk.metadata.get("content_hash") == doc.content_hash
                   and h.chunk.metadata.get('embedding_model') == self._embedder.model_name for h in hits):
            raise RuntimeError(f"Approved index missing or stale: {doc.policy_id} v{doc.version}")

    def check_ready(self):
        documents = self._active_documents()
        missing = {"POL-001", "POL-002", "POL-003", "POL-004"} - {d.policy_id for d in documents}
        if missing:
            raise RuntimeError(f"Missing mandatory approved policies: {sorted(missing)}")
        for doc in documents:
            self._verify_indexed(doc)

    def get_document(self, policy_id: str, version: str | None = None) -> Any | None:
        """Retrieve the authoritative policy document model by ID and optional version."""
        return self._doc_source.get_document(policy_id, version=version)

    def retrieve_mandatory(
        self,
        policy_id: str | None = None,
        intent: str | None = None,
        event: str | None = None,
    ) -> list[PolicyEvidence]:
        """Deterministically lookup approved policies based on rules, intent, or event triggers."""
        documents = self._active_documents()
        results: list[PolicyEvidence] = []
        seen_policies: set[str] = set()

        for doc in documents:
            match = False
            if policy_id and doc.policy_id == policy_id.strip().upper():
                match = True
            elif intent and intent in doc.applicable_intents:
                match = True
            elif intent == 'unblock_card' and doc.policy_id == 'POL-001':
                match = True
            elif event and event in doc.triggering_events:
                match = True
            elif doc.policy_id == "POL-004":
                # POL-004 is always mandatory for state-changing operations
                match = True

            if match and doc.policy_id not in seen_policies:
                if self.strict_index:
                    self._verify_indexed(doc)
                seen_policies.add(doc.policy_id)
                results.append(
                    PolicyEvidence(
                        policy_id=doc.policy_id,
                        version=doc.version,
                        title=doc.title,
                        section_title="Mandatory Document Rules",
                        content=doc.raw_content[:4000],
                        source=doc.source,
                        score=1.0,
                        mandatory=True,
                        effective_date=doc.effective_date,
                        restrictions=doc.restrictions,
                        required_actions=doc.required_actions,
                        conditions=doc.conditions,
                    )
                )

        return results

    def retrieve(
        self,
        intent: str,
        facts: dict[str, Any],
        query: str | None = None,
        limit: int = 5,
    ) -> list[PolicyEvidence]:
        """Retrieve all applicable policy evidence using hybrid deterministic and semantic search."""
        evidence_list: list[PolicyEvidence] = []
        seen_keys: set[str] = set()

        # 1. Deterministic mandatory retrieval
        event = facts.get("last_domain_event")
        mandatory_hits = self.retrieve_mandatory(intent=intent, event=event)
        for m in mandatory_hits:
            key = f"{m.policy_id}:{m.section_title}"
            seen_keys.add(key)
            evidence_list.append(m)

        # 2. Construct PII-safe search query
        query_parts = [intent.replace("_", " ")]
        if facts.get("has_blocked_card"):
            query_parts.append("blocked card exists on account")
        if facts.get("is_temporary_freeze"):
            query_parts.append("temporary freeze unblock eligibility")
        if facts.get("is_permanently_compromised"):
            query_parts.append("permanently compromised stolen card replacement")
        if query:
            # Strip potential card numbers or account numbers from user query for safe embedding
            clean_q = " ".join([w for w in query.split() if not w.isdigit() and len(w) < 30])
            query_parts.append(clean_q)

        search_query = " ".join(query_parts)

        # 3. Semantic vector search
        try:
            query_vec = self._embedder.embed_query(search_query)
            search_results = self._store.search(query_vec, limit=limit, score_threshold=0.1)
            active = {d.policy_id: d for d in self._active_documents()}

            for hit in search_results:
                chunk = hit.chunk
                doc = active.get(chunk.policy_id)
                if not doc or doc.version != chunk.version or not chunk.metadata.get("approved", True) or chunk.metadata.get("status", "approved") != "approved":
                    continue
                if self.strict_index and (chunk.metadata.get("content_hash") != doc.content_hash
                                          or chunk.metadata.get('embedding_model') != self._embedder.model_name):
                    continue
                key = f"{chunk.policy_id}:{chunk.title}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                meta = chunk.metadata or {}
                evidence_list.append(
                    PolicyEvidence(
                        policy_id=chunk.policy_id,
                        version=chunk.version,
                        title=meta.get("title", chunk.title),
                        section_title=chunk.title,
                        content=chunk.content,
                        source=chunk.source,
                        score=hit.score,
                        mandatory=False,
                        effective_date=meta.get("effective_date", ""),
                        restrictions=meta.get("restrictions", []),
                        required_actions=meta.get("required_actions", []),
                        conditions=meta.get("conditions", {}),
                    )
                )
        except Exception as exc:
            if self.strict_index:
                raise RuntimeError("Policy vector retrieval unavailable") from exc
            logger.warning("Semantic vector search encountered non-fatal error: %s", exc)

        return evidence_list
