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
        documents = self._doc_source.list_documents(approved_only=True)
        results: list[PolicyEvidence] = []
        seen_policies: set[str] = set()

        for doc in documents:
            match = False
            if policy_id and doc.policy_id == policy_id.strip().upper():
                match = True
            elif intent and intent in doc.applicable_intents:
                match = True
            elif event and event in doc.triggering_events:
                match = True
            elif doc.policy_id == "POL-004":
                # POL-004 is always mandatory for state-changing operations
                match = True

            if match and doc.policy_id not in seen_policies:
                seen_policies.add(doc.policy_id)
                results.append(
                    PolicyEvidence(
                        policy_id=doc.policy_id,
                        version=doc.version,
                        title=doc.title,
                        section_title="Mandatory Document Rules",
                        content=doc.raw_content[:1000],
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

            for hit in search_results:
                chunk = hit.chunk
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
            logger.warning("Semantic vector search encountered non-fatal error: %s", exc)

        return evidence_list
