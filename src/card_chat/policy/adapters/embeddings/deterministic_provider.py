"""Deterministic local embedding provider adapter for testing and lightweight fallback."""
from __future__ import annotations

import math
import re
from typing import Sequence

from ...ports import EmbeddingProvider


class DeterministicLocalEmbeddingProvider(EmbeddingProvider):
    """Deterministic, local 256-dimensional subword and banking-domain embedder.

    Produces normalized float vectors using offline mathematical hashing without
    external model downloads, making it an ideal deterministic test double.
    """

    def __init__(self, dimension: int = 256):
        self._dim = dimension
        self._name = "deterministic-local-banking-v1"
        self._anchors = [
            "card", "account", "block", "frozen", "temporary", "unblock",
            "replace", "replacement", "issue", "issuance", "lost", "stolen",
            "compromise", "fraud", "destroy", "security", "authorization",
            "confirm", "idempotent", "restriction", "customer", "prohibit",
            "eligible", "permission", "review", "active", "status", "nonce",
        ]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._name

    def _embed_single(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        clean = text.lower().strip()
        tokens = re.findall(r"\w+", clean)
        if not tokens:
            return vec

        for token in tokens:
            for n in (3, 4):
                if len(token) >= n:
                    for i in range(len(token) - n + 1):
                        ngram = token[i : i + n]
                        idx = hash(ngram) % self._dim
                        vec[idx] += 1.0

        for a_idx, anchor in enumerate(self._anchors):
            if anchor in clean:
                slot = (a_idx * 7 + 13) % self._dim
                vec[slot] += 3.5

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-9:
            vec = [x / norm for x in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_single(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_single(text)
