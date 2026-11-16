"""Qdrant VectorStore adapter for enterprise vector indexing and retrieval."""
from __future__ import annotations

import logging
from typing import Any
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models as rest_models
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from ...models import PolicyChunk, SearchResult
from ...ports import VectorStore

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "banking_policies"


class QdrantVectorStore(VectorStore):
    """Qdrant vector store adapter supporting Docker service and local/in-memory modes."""

    def __init__(
        self,
        url: str | None = None,
        path: str | None = None,
        client: QdrantClient | None = None,
        collection_name: str = DEFAULT_COLLECTION,
    ):
        self.collection_name = collection_name
        self._url = url
        self._path = path
        if client is not None:
            self._client = client
        elif url:
            logger.info("Connecting to Qdrant service at: %s", url)
            self._client = QdrantClient(url=url)
        elif path:
            logger.info("Using local Qdrant storage at path: %s", path)
            self._client = QdrantClient(path=path)
        else:
            logger.info("Using embedded in-memory Qdrant client")
            self._client = QdrantClient(":memory:")

    @property
    def client(self) -> QdrantClient:
        return self._client

    def initialize(self, dimension: int) -> None:
        """Create or verify the Qdrant collection with expected dimension and Cosine metric."""
        try:
            collections = self._client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)
            if not exists:
                logger.info(
                    "Creating Qdrant collection '%s' (dim=%d, metric=Cosine)",
                    self.collection_name,
                    dimension,
                )
                self._client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
                )
            else:
                info = self._client.get_collection(self.collection_name)
                current_size = info.config.params.vectors.size
                if current_size != dimension:
                    logger.warning(
                        "Collection '%s' exists with dimension %d, recreating for dimension %d",
                        self.collection_name,
                        current_size,
                        dimension,
                    )
                    self._client.delete_collection(self.collection_name)
                    self._client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
                    )
        except Exception as exc:
            logger.error("Failed to initialize Qdrant collection '%s': %s", self.collection_name, exc)
            raise RuntimeError(f"Qdrant collection initialization error: {exc}") from exc

    def upsert_chunks(self, chunks: list[PolicyChunk], embeddings: list[list[float]]) -> None:
        """Upsert policy chunks and their embedding vectors into Qdrant."""
        if not chunks:
            return
        if len(chunks) != len(embeddings):
            raise ValueError(f"Chunk count ({len(chunks)}) does not match embedding count ({len(embeddings)})")

        points: list[PointStruct] = []
        for chunk, vector in zip(chunks, embeddings):
            # Deterministic UUID from chunk_id
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk.chunk_id))
            payload = {
                "chunk_id": chunk.chunk_id,
                "policy_id": chunk.policy_id,
                "version": chunk.version,
                "chunk_index": chunk.chunk_index,
                "title": chunk.title,
                "content": chunk.content,
                "source": chunk.source,
                "metadata": chunk.metadata,
                "applicable_intents": chunk.metadata.get("applicable_intents", []),
                "triggering_events": chunk.metadata.get("triggering_events", []),
                "effective_date": chunk.metadata.get("effective_date", ""),
                "status": chunk.metadata.get("status", "approved"),
                "approved": chunk.metadata.get("approved", True),
            }
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        try:
            self._client.upsert(collection_name=self.collection_name, points=points, wait=True)
            logger.debug("Successfully upserted %d points to Qdrant '%s'", len(points), self.collection_name)
        except Exception as exc:
            logger.error("Failed to upsert points into Qdrant '%s': %s", self.collection_name, exc)
            raise RuntimeError(f"Qdrant upsert error: {exc}") from exc

    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        score_threshold: float = 0.0,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Search Qdrant using vector similarity and optional metadata filters."""
        query_filter: Filter | None = None
        if filter_metadata:
            conditions = []
            for key, val in filter_metadata.items():
                if isinstance(val, (str, int, float, bool)):
                    conditions.append(FieldCondition(key=key, match=MatchValue(value=val)))
            if conditions:
                query_filter = Filter(must=conditions)

        try:
            hits = self._client.query_points(
                collection_name=self.collection_name,
                query=query_embedding,
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold if score_threshold > 0.0 else None,
            ).points

            results: list[SearchResult] = []
            for hit in hits:
                payload = hit.payload or {}
                chunk = PolicyChunk(
                    chunk_id=payload.get("chunk_id", str(hit.id)),
                    policy_id=payload.get("policy_id", ""),
                    version=payload.get("version", "1.0.0"),
                    chunk_index=payload.get("chunk_index", 0),
                    title=payload.get("title", ""),
                    content=payload.get("content", ""),
                    source=payload.get("source", ""),
                    metadata=payload.get("metadata", {}),
                    score=float(hit.score or 0.0),
                )
                results.append(SearchResult(chunk=chunk, score=float(hit.score or 0.0)))
            return results
        except Exception as exc:
            logger.error("Failed to search Qdrant collection '%s': %s", self.collection_name, exc)
            raise RuntimeError(f"Qdrant search error: {exc}") from exc

    def delete_by_policy_id(self, policy_id: str) -> int:
        """Delete all points associated with a specific policy ID."""
        filter_obj = Filter(
            must=[FieldCondition(key="policy_id", match=MatchValue(value=policy_id.strip().upper()))]
        )
        try:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=filter_obj,
                wait=True,
            )
            return 1
        except Exception as exc:
            logger.error("Failed to delete points for policy '%s': %s", policy_id, exc)
            raise RuntimeError(f"Qdrant deletion error: {exc}") from exc

    def count(self) -> int:
        """Count total vectors in collection."""
        try:
            res = self._client.count(collection_name=self.collection_name)
            return res.count
        except Exception:
            return 0
