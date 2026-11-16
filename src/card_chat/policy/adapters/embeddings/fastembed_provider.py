"""FastEmbed EmbeddingProvider adapter for local CPU inference."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ...ports import EmbeddingProvider

logger = logging.getLogger(__name__)

# Default model: BAAI/bge-small-en-v1.5 produces 384-dimensional dense vectors
DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_DIMENSION = 384


def _resolve_writable_cache_dir() -> str | None:
    """Ensure FastEmbed and HuggingFace cache directories are writable."""
    explicit = os.getenv("FASTEMBED_CACHE_PATH")
    if explicit:
        try:
            Path(explicit).mkdir(parents=True, exist_ok=True)
            return explicit
        except (OSError, PermissionError):
            pass

    candidates = [
        Path.home() / ".cache" / "fastembed",
        Path("/data/cache/fastembed"),
        Path("/tmp/cache/fastembed"),
        Path("data/cache/fastembed"),
    ]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            test_file = c / ".test_write"
            test_file.touch()
            test_file.unlink()
            if "HF_HOME" not in os.environ:
                hf_dir = c.parent / "huggingface"
                hf_dir.mkdir(parents=True, exist_ok=True)
                os.environ["HF_HOME"] = str(hf_dir)
            return str(c)
        except (OSError, PermissionError):
            continue
    return None


class FastEmbedEmbeddingProvider(EmbeddingProvider):
    """Local CPU-based embedding generator using FastEmbed (ONNX runtime).

    Guarantees that raw text and domain context never leave the local environment,
    preserving enterprise PII boundaries and zero network latency during embedding.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        cache_dir: str | None = None,
        threads: int | None = None,
    ):
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._threads = threads
        self._model: Any = None
        self._dimension = DEFAULT_DIMENSION

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
                logger.info("Initializing FastEmbed model: %s", self._model_name)
                kwargs: dict[str, Any] = {"model_name": self._model_name}
                cache_path = self._cache_dir or _resolve_writable_cache_dir()
                if cache_path:
                    kwargs["cache_dir"] = cache_path
                if self._threads:
                    kwargs["threads"] = self._threads
                self._model = TextEmbedding(**kwargs)
                # Verify dimension with a quick warmup
                sample = list(self._model.embed(["health check"]))
                if sample:
                    self._dimension = len(sample[0])
                logger.info("FastEmbed model ready. Vector dimension: %d", self._dimension)
            except Exception as exc:
                logger.error("Failed to initialize FastEmbed model %s: %s", self._model_name, exc)
                raise RuntimeError(f"FastEmbed initialization error: {exc}") from exc
        return self._model

    @property
    def dimension(self) -> int:
        self._ensure_model()
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        try:
            generator = model.embed(texts)
            return [list(vec) for vec in generator]
        except Exception as exc:
            logger.error("Failed to generate document embeddings: %s", exc)
            raise RuntimeError(f"Embedding error: {exc}") from exc

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            return [0.0] * self._dimension
        model = self._ensure_model()
        try:
            # FastEmbed passage/query embedding
            generator = model.embed([text])
            vectors = [list(vec) for vec in generator]
            return vectors[0] if vectors else [0.0] * self._dimension
        except Exception as exc:
            logger.error("Failed to generate query embedding: %s", exc)
            raise RuntimeError(f"Query embedding error: {exc}") from exc
