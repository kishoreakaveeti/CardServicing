"""Independent policy ingestion pipeline with chunking, validation, and auditability."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any

from ..adapters.document_source import parse_policy_file
from ..models import PolicyChunk, PolicyDocument
from ..ports import DocumentSource, EmbeddingProvider, VectorStore

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_LOG = Path("data/policy_audit.json")


def chunk_policy_markdown(doc: PolicyDocument, body: str) -> list[PolicyChunk]:
    """Split policy markdown into section-level semantic chunks."""
    lines = body.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = doc.title
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if current_lines:
                sections.append((current_title, current_lines))
                current_lines = []
            current_title = line.strip("# ").strip()
        elif line.startswith("# "):
            if not current_lines:
                current_title = line.strip("# ").strip()
            else:
                current_lines.append(line)
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_title, current_lines))

    chunks: list[PolicyChunk] = []
    chunk_idx = 0
    inherited_meta = {
        "title": doc.title,
        "effective_date": doc.effective_date,
        "status": doc.status,
        "approved": doc.approved,
        "applicable_intents": doc.applicable_intents,
        "triggering_events": doc.triggering_events,
        "conditions": doc.conditions,
        "required_actions": doc.required_actions,
        "restrictions": doc.restrictions,
        "content_hash": doc.content_hash,
    }

    for section_title, sec_lines in sections:
        text = "\n".join(sec_lines).strip()
        if not text:
            continue

        # Sub-chunk if section is excessively large (> 1500 chars)
        max_chunk_size = 1200
        if len(text) > max_chunk_size:
            paras = text.split("\n\n")
            buffer = ""
            sub_idx = 0
            for para in paras:
                if len(buffer) + len(para) < max_chunk_size:
                    buffer = (buffer + "\n\n" + para).strip()
                else:
                    if buffer:
                        chunks.append(
                            PolicyChunk(
                                chunk_id=f"{doc.policy_id}#c{chunk_idx}",
                                policy_id=doc.policy_id,
                                version=doc.version,
                                chunk_index=chunk_idx,
                                title=f"{section_title} (part {sub_idx + 1})",
                                content=buffer,
                                metadata=inherited_meta,
                                source=doc.source,
                            )
                        )
                        chunk_idx += 1
                        sub_idx += 1
                    buffer = para.strip()
            if buffer:
                chunks.append(
                    PolicyChunk(
                        chunk_id=f"{doc.policy_id}#c{chunk_idx}",
                        policy_id=doc.policy_id,
                        version=doc.version,
                        chunk_index=chunk_idx,
                        title=f"{section_title} (part {sub_idx + 1})",
                        content=buffer,
                        metadata=inherited_meta,
                        source=doc.source,
                    )
                )
                chunk_idx += 1
        else:
            chunks.append(
                PolicyChunk(
                    chunk_id=f"{doc.policy_id}#c{chunk_idx}",
                    policy_id=doc.policy_id,
                    version=doc.version,
                    chunk_index=chunk_idx,
                    title=section_title,
                    content=text,
                    metadata=inherited_meta,
                    source=doc.source,
                )
            )
            chunk_idx += 1

    return chunks


class PolicyIngestionPipeline:
    """Manages document validation, chunking, embedding, vector store indexing, and audit logging."""

    def __init__(
        self,
        document_source: DocumentSource,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        audit_log_path: Path | str = DEFAULT_AUDIT_LOG,
    ):
        self.source = document_source
        self.embedder = embedding_provider
        self.store = vector_store
        self.audit_path = Path(audit_log_path)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_document(self, doc: PolicyDocument) -> None:
        """Validate document metadata, approval status, and structured rules."""
        if not doc.policy_id:
            raise ValueError("Document missing policy_id")
        if not doc.version:
            raise ValueError(f"Document {doc.policy_id} missing version")
        if not doc.approved or doc.status != 'approved':
            raise ValueError(f"Document {doc.policy_id} is not approved for production enforcement (status={doc.status})")
        if not doc.effective_date:
            raise ValueError(f"Document {doc.policy_id} missing effective_date")

    def _record_audit(self, entry: dict[str, Any]) -> None:
        entries: list[dict[str, Any]] = []
        if self.audit_path.exists():
            try:
                entries = json.loads(self.audit_path.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        entries.append(entry)
        self.audit_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    def get_audit_trail(self) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        try:
            return json.loads(self.audit_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def initialize(self) -> None:
        """Initialize the vector store collection with the embedding provider's dimension."""
        dim = self.embedder.dimension
        logger.info("Initializing vector store with dimension: %d", dim)
        self.store.initialize(dim)

    def ingest_document(self, doc: PolicyDocument, body_markdown: str, force: bool = False) -> int:
        """Validate, chunk, embed, and index a single policy document idempotently."""
        self.validate_document(doc)
        chunks = chunk_policy_markdown(doc, body_markdown)
        if not chunks:
            logger.warning("No text chunks generated for policy: %s", doc.policy_id)
            return 0

        # Generate embeddings
        texts = [f"Title: {c.title}\nPolicy: {c.policy_id} v{c.version}\n{c.content}" for c in chunks]
        embeddings = self.embedder.embed_documents(texts)
        for chunk in chunks:
            chunk.metadata['embedding_model'] = self.embedder.model_name

        # Atomic replacement: delete existing points for this policy_id then upsert
        self.store.delete_by_policy_id(doc.policy_id)
        self.store.upsert_chunks(chunks, embeddings)

        self._record_audit({
            "action": "ingest_document",
            "policy_id": doc.policy_id,
            "version": doc.version,
            "content_hash": doc.content_hash,
            "chunk_count": len(chunks),
            "model_name": self.embedder.model_name,
            "dimension": self.embedder.dimension,
        })
        logger.info("Ingested policy %s v%s (%d chunks)", doc.policy_id, doc.version, len(chunks))
        return len(chunks)

    def ingest_file(self, file_path: Path | str, force: bool = False) -> int:
        doc, body = parse_policy_file(file_path)
        return self.ingest_document(doc, body, force=force)

    def ingest_all(self, force: bool = False) -> dict[str, int]:
        """Ingest all approved policy documents from the DocumentSource."""
        self.initialize()
        docs = self.source.list_documents(approved_only=True)
        results: dict[str, int] = {}
        for doc in docs:
            # Re-read raw content from source path if available
            body = doc.raw_content
            parts = body.split("---", 2)
            body_md = parts[2].strip() if len(parts) >= 3 else body
            count = self.ingest_document(doc, body_md, force=force)
            results[doc.policy_id] = count
        return results

    def inspect(self) -> dict[str, Any]:
        """Inspect the current vector store status, indexed documents, and audit records."""
        docs = self.source.list_documents(approved_only=False)
        return {
            "total_vectors": self.store.count(),
            "embedding_model": self.embedder.model_name,
            "embedding_dimension": self.embedder.dimension,
            "available_documents": [
                {
                    "policy_id": d.policy_id,
                    "version": d.version,
                    "title": d.title,
                    "approved": d.approved,
                    "status": d.status,
                    "effective_date": d.effective_date,
                    "content_hash": d.content_hash,
                }
                for d in docs
            ],
            "audit_trail_count": len(self.get_audit_trail()),
        }
