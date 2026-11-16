"""Deterministic, lightweight local semantic embedding model.

Provides fast, reproducible, offline semantic dense vector embeddings without
external API keys, PyTorch dependencies, or sending raw customer data over networks.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence
import numpy as np

# Domain anchor terms with higher semantic significance in banking policy
BANKING_ANCHORS = {
    # Issuance domain
    "issue": 0, "issuance": 0, "additional": 1, "card": 2, "cards": 2, "create": 3,
    # Blocking domain
    "block": 4, "blocked": 4, "blocking": 4, "freeze": 5, "frozen": 5, "temporary": 5,
    "unblock": 6, "unblocking": 6, "unfreeze": 6, "restore": 6, "reactivate": 6,
    # Fraud & loss domain
    "lost": 7, "stolen": 8, "fraud": 9, "compromised": 10, "compromise": 10, "destroy": 11,
    # Replacement domain
    "replace": 12, "replacement": 12, "reissue": 12,
    # Governance & safety
    "confirm": 13, "confirmation": 13, "authorise": 14, "authorize": 14, "authorization": 14,
    "idempotent": 15, "idempotency": 15, "prohibit": 16, "prohibited": 16, "restriction": 17,
    "eligible": 18, "eligibility": 18, "mandatory": 19, "policy": 20,
}


class DeterministicLocalEmbedder:
    """Local, lightweight semantic embedding engine generating dense vectors.

    Combines token hash projections, character subword n-grams (3-5 chars),
    and banking domain anchor projections into an L2-normalized float32 vector.
    """

    def __init__(self, dimension: int = 256):
        self.dimension = dimension

    def _tokenize(self, text: str) -> list[str]:
        return [t for t in re.findall(r"[a-z0-9_-]+", text.lower()) if len(t) > 1]

    def _get_ngrams(self, word: str) -> list[str]:
        ngrams = []
        for n in (3, 4, 5):
            if len(word) >= n:
                for i in range(len(word) - n + 1):
                    ngrams.append(word[i:i + n])
        return ngrams

    def embed(self, text: str) -> np.ndarray:
        """Embed a single text string into a normalized dense vector."""
        vec = np.zeros(self.dimension, dtype=np.float32)
        tokens = self._tokenize(text)
        if not tokens:
            return vec

        # 1. Word token hashing with term frequency
        token_counts: dict[str, int] = {}
        for token in tokens:
            token_counts[token] = token_counts.get(token, 0) + 1

        for token, count in token_counts.items():
            tf = 1.0 + math.log(count)
            # MD5 hash to stable index
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % self.dimension
            sign = 1.0 if (h & 1) else -1.0
            vec[idx] += sign * tf * 2.0

            # Domain anchor boosts
            if token in BANKING_ANCHORS:
                anchor_idx = BANKING_ANCHORS[token] % self.dimension
                vec[anchor_idx] += tf * 4.0

            # 2. Subword character n-grams to handle morphological variations
            ngrams = self._get_ngrams(token)
            for ng in ngrams:
                h_ng = int(hashlib.md5(ng.encode("utf-8")).hexdigest()[:8], 16)
                idx_ng = h_ng % self.dimension
                sign_ng = 1.0 if (h_ng & 1) else -1.0
                vec[idx_ng] += sign_ng * 0.5

        # 3. L2 Normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm

        return vec

    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        """Embed multiple text strings into a 2D matrix of shape (len(texts), dimension)."""
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        vectors = [self.embed(t) for t in texts]
        return np.vstack(vectors)

    def similarity(self, text_a: str, text_b: str) -> float:
        """Calculate cosine similarity between two texts."""
        v_a = self.embed(text_a)
        v_b = self.embed(text_b)
        return float(np.dot(v_a, v_b))
