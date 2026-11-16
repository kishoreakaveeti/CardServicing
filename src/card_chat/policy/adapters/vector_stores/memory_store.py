"""In-memory VectorStore adapter for lightweight testing."""
from __future__ import annotations

import math
from typing import Any

from ...models import PolicyChunk, SearchResult
from ...ports import VectorStore


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore(VectorStore):
    """Test double VectorStore storing vectors and chunks in Python dictionaries."""

    def __init__(self) -> None:
        self._chunks: dict[str, PolicyChunk] = {}
        self._embeddings: dict[str, list[float]] = {}
        self._dimension = 0

    def initialize(self, dimension: int) -> None:
        self._dimension = dimension

    def upsert_chunks(self, chunks: list[PolicyChunk], embeddings: list[list[float]]) -> None:
        for chunk, emb in zip(chunks, embeddings):
            self._chunks[chunk.chunk_id] = chunk
            self._embeddings[chunk.chunk_id] = emb

    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        score_threshold: float = 0.0,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        hits: list[SearchResult] = []
        for chunk_id, chunk in self._chunks.items():
            if filter_metadata:
                match = True
                for k, v in filter_metadata.items():
                    if k == "policy_id" and chunk.policy_id != v:
                        match = False
                        break
                    elif k in chunk.metadata and chunk.metadata[k] != v:
                        match = False
                        break
                if not match:
                    continue

            emb = self._embeddings.get(chunk_id)
            if not emb:
                continue
            sim = _cosine_similarity(query_embedding, emb)
            if sim >= score_threshold:
                hits.append(SearchResult(chunk=chunk, score=sim))

        hits.sort(key=lambda s: s.score, reverse=True)
        return hits[:limit]

    def delete_by_policy_id(self, policy_id: str) -> int:
        norm_id = policy_id.strip().upper()
        to_del = [cid for cid, c in self._chunks.items() if c.policy_id == norm_id]
        for cid in to_del:
            self._chunks.pop(cid, None)
            self._embeddings.pop(cid, None)
        return len(to_del)

    def count(self) -> int:
        return len(self._chunks)
