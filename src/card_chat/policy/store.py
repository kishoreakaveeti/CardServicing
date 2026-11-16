"""Persistent SQLite vector and metadata store for banking company policies."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence
import numpy as np
import yaml

from card_chat.policy.embeddings import DeterministicLocalEmbedder
from card_chat.policy.models import PolicyChunk, PolicyDocument

logger = logging.getLogger(__name__)


def parse_markdown_policy(file_path: Path | str) -> tuple[PolicyDocument, list[tuple[str, str]]]:
    """Parse a versioned policy markdown file with YAML frontmatter and section chunks."""
    path = Path(file_path)
    text = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    frontmatter_dict: dict[str, Any] = {}
    body = text

    # Parse YAML frontmatter if delimited by '---'
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw_yaml = parts[1]
            body = parts[2].strip()
            try:
                frontmatter_dict = yaml.safe_load(raw_yaml) or {}
            except Exception as e:
                logger.warning("Failed to parse YAML frontmatter in %s: %s", path, e)

    policy_id = frontmatter_dict.get("policy_id") or path.stem.split("-")[0].upper()
    version = str(frontmatter_dict.get("version", "1.0.0"))
    effective_date = str(frontmatter_dict.get("effective_date", "2026-01-01"))
    title = frontmatter_dict.get("title") or path.stem.replace("-", " ").title()

    doc = PolicyDocument(
        policy_id=policy_id,
        version=version,
        effective_date=effective_date,
        title=title,
        applicable_intents=frontmatter_dict.get("applicable_intents", []),
        triggering_events=frontmatter_dict.get("triggering_events", []),
        conditions=frontmatter_dict.get("conditions", {}),
        required_actions=frontmatter_dict.get("required_actions", []),
        restrictions=frontmatter_dict.get("restrictions", []),
        source=str(path.as_posix()),
        raw_content=text,
        content_hash=content_hash,
    )

    # Chunking: split on markdown headers '## '
    chunks: list[tuple[str, str]] = []
    lines = body.split("\n")
    current_title = title
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if current_lines:
                chunk_text = "\n".join(current_lines).strip()
                if chunk_text:
                    chunks.append((current_title, chunk_text))
            current_title = line.lstrip("#").strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        chunk_text = "\n".join(current_lines).strip()
        if chunk_text:
            chunks.append((current_title, chunk_text))

    if not chunks:
        chunks.append((title, body))

    return doc, chunks


class PolicyStore:
    """Persistent SQLite-backed vector index and metadata store for company policies."""

    def __init__(self, db_path: str = ":memory:", embedder: DeterministicLocalEmbedder | None = None):
        self.db_path = db_path
        self.embedder = embedder or DeterministicLocalEmbedder(dimension=256)
        self._conn: sqlite3.Connection | None = None
        if db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS policy_documents (
                    policy_id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_content TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS policy_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    FOREIGN KEY (policy_id) REFERENCES policy_documents(policy_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_policy_id ON policy_chunks(policy_id);
            """)

    def index_document(self, file_path: Path | str, force: bool = False) -> bool:
        """Ingest or update a single policy document. Returns True if indexed/updated."""
        path = Path(file_path)
        if not path.is_file():
            return False

        doc, chunks = parse_markdown_policy(path)

        with self._get_connection() as conn:
            # Check existing hash
            cur = conn.cursor()
            cur.execute("SELECT content_hash, version FROM policy_documents WHERE policy_id = ?", (doc.policy_id,))
            row = cur.fetchone()

            if row and row["content_hash"] == doc.content_hash and not force:
                logger.debug("Policy %s unchanged (%s); skipping reindex.", doc.policy_id, doc.content_hash[:8])
                return False

            now = datetime.now(timezone.utc).isoformat()
            metadata_dict = {
                "applicable_intents": doc.applicable_intents,
                "triggering_events": doc.triggering_events,
                "conditions": doc.conditions,
                "required_actions": doc.required_actions,
                "restrictions": doc.restrictions,
                "source": doc.source,
                "title": doc.title,
            }
            metadata_json = json.dumps(metadata_dict)

            # Purge existing chunks to avoid stale embeddings
            cur.execute("DELETE FROM policy_chunks WHERE policy_id = ?", (doc.policy_id,))

            cur.execute("""
                INSERT INTO policy_documents (
                    policy_id, version, effective_date, title, source,
                    content_hash, metadata_json, raw_content, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id) DO UPDATE SET
                    version = excluded.version,
                    effective_date = excluded.effective_date,
                    title = excluded.title,
                    source = excluded.source,
                    content_hash = excluded.content_hash,
                    metadata_json = excluded.metadata_json,
                    raw_content = excluded.raw_content,
                    indexed_at = excluded.indexed_at
            """, (
                doc.policy_id, doc.version, doc.effective_date, doc.title, doc.source,
                doc.content_hash, metadata_json, doc.raw_content, now
            ))

            # Embed and insert chunks
            for idx, (chunk_title, chunk_content) in enumerate(chunks):
                chunk_id = f"{doc.policy_id}#chunk-{idx}"
                full_text_for_embedding = f"{doc.policy_id} {doc.title} {chunk_title}\n{chunk_content}"
                emb = self.embedder.embed(full_text_for_embedding)

                chunk_meta = dict(metadata_dict)
                chunk_meta["chunk_title"] = chunk_title
                chunk_meta["chunk_index"] = idx

                cur.execute("""
                    INSERT INTO policy_chunks (
                        chunk_id, policy_id, version, chunk_index,
                        title, content, metadata_json, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chunk_id, doc.policy_id, doc.version, idx,
                    chunk_title, chunk_content, json.dumps(chunk_meta), emb.tobytes()
                ))

            conn.commit()
            logger.info("Indexed policy %s v%s (%d chunks) from %s", doc.policy_id, doc.version, len(chunks), doc.source)
            return True

    def index_directory(self, dir_path: Path | str, force: bool = False) -> dict[str, int]:
        """Scan a directory for Markdown policy files, indexing new/modified and removing deleted ones."""
        directory = Path(dir_path)
        if not directory.exists() or not directory.is_dir():
            logger.warning("Policy directory %s does not exist", dir_path)
            return {"indexed": 0, "deleted": 0, "total": 0}

        md_files = list(directory.glob("*.md"))
        active_policy_ids = set()
        indexed_count = 0

        for file in md_files:
            try:
                # Read policy id from file
                doc, _ = parse_markdown_policy(file)
                active_policy_ids.add(doc.policy_id)
                if self.index_document(file, force=force):
                    indexed_count += 1
            except Exception as exc:
                logger.error("Error reading policy file %s: %s", file, exc)

        # Remove deleted policies from database
        deleted_count = 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT policy_id FROM policy_documents")
            existing = [r["policy_id"] for r in cur.fetchall()]
            for pid in existing:
                if pid not in active_policy_ids:
                    cur.execute("DELETE FROM policy_documents WHERE policy_id = ?", (pid,))
                    cur.execute("DELETE FROM policy_chunks WHERE policy_id = ?", (pid,))
                    deleted_count += 1
                    logger.info("Purged deleted policy %s from index", pid)
            conn.commit()

        return {"indexed": indexed_count, "deleted": deleted_count, "total": len(active_policy_ids)}

    def get_document(self, policy_id: str) -> PolicyDocument | None:
        """Fetch full document by policy ID."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM policy_documents WHERE policy_id = ?", (policy_id,))
            row = cur.fetchone()
            if not row:
                return None
            meta = json.loads(row["metadata_json"])
            return PolicyDocument(
                policy_id=row["policy_id"],
                version=row["version"],
                effective_date=row["effective_date"],
                title=row["title"],
                applicable_intents=meta.get("applicable_intents", []),
                triggering_events=meta.get("triggering_events", []),
                conditions=meta.get("conditions", {}),
                required_actions=meta.get("required_actions", []),
                restrictions=meta.get("restrictions", []),
                source=row["source"],
                raw_content=row["raw_content"],
                content_hash=row["content_hash"],
            )

    def retrieve_mandatory(
        self,
        policy_id: str | None = None,
        intent: str | None = None,
        event_type: str | None = None,
        effective_date_filter: str | None = None,
    ) -> list[PolicyChunk]:
        """Deterministically retrieve mandatory policies by policy ID, intent, and triggering event.

        Guarantees that mandatory governance restrictions are NEVER omitted due to vector similarity thresholds.
        """
        results: list[PolicyChunk] = []
        with self._get_connection() as conn:
            cur = conn.cursor()
            query = "SELECT c.*, d.source FROM policy_chunks c JOIN policy_documents d ON c.policy_id = d.policy_id WHERE 1=1"
            params: list[Any] = []

            if policy_id:
                query += " AND c.policy_id = ?"
                params.append(policy_id)

            cur.execute(query, params)
            rows = cur.fetchall()

            for row in rows:
                meta = json.loads(row["metadata_json"])
                doc_effective = meta.get("effective_date", "2026-01-01")

                # Filter by effective date if specified
                if effective_date_filter and doc_effective > effective_date_filter:
                    continue

                intents = meta.get("applicable_intents", [])
                events = meta.get("triggering_events", [])

                match_intent = False
                if intent:
                    match_intent = intent in intents or "all" in intents
                elif not policy_id and not event_type:
                    match_intent = True

                match_event = False
                if event_type:
                    match_event = event_type in events or "all" in events
                elif not policy_id and not intent:
                    match_event = True

                # Include if exact policy_id, or if intent matched, or if event matched
                if policy_id or match_intent or match_event:
                    results.append(
                        PolicyChunk(
                            chunk_id=row["chunk_id"],
                            policy_id=row["policy_id"],
                            version=row["version"],
                            chunk_index=row["chunk_index"],
                            title=row["title"],
                            content=row["content"],
                            metadata=meta,
                            source=row["source"],
                            score=1.0,  # Mandatory matches have top confidence
                        )
                    )

        return results

    def search(
        self,
        query: str,
        top_k: int = 3,
        metadata_filter: dict[str, Any] | None = None,
        min_similarity: float = 0.0,
    ) -> list[PolicyChunk]:
        """Perform semantic vector similarity search against indexed policy chunks."""
        query_vec = self.embedder.embed(query)

        candidates: list[PolicyChunk] = []
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT c.*, d.source FROM policy_chunks c JOIN policy_documents d ON c.policy_id = d.policy_id")
            rows = cur.fetchall()

            for row in rows:
                meta = json.loads(row["metadata_json"])

                # Metadata filtering if requested
                if metadata_filter:
                    skip = False
                    for k, v in metadata_filter.items():
                        if k in meta and meta[k] != v:
                            skip = True
                            break
                    if skip:
                        continue

                # Load embedding
                emb_bytes = row["embedding"]
                emb = np.frombuffer(emb_bytes, dtype=np.float32)
                sim = float(np.dot(emb, query_vec))

                if sim >= min_similarity:
                    chunk = PolicyChunk(
                        chunk_id=row["chunk_id"],
                        policy_id=row["policy_id"],
                        version=row["version"],
                        chunk_index=row["chunk_index"],
                        title=row["title"],
                        content=row["content"],
                        metadata=meta,
                        source=row["source"],
                        score=sim,
                    )
                    candidates.append(chunk)

        # Sort descending by similarity
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:top_k]

    def retrieve(
        self,
        query: str,
        intent: str | None = None,
        event_type: str | None = None,
        top_k: int = 3,
    ) -> list[PolicyChunk]:
        """Hybrid retrieval combining mandatory deterministic policy lookups and semantic vector ranking."""
        seen_chunks: set[str] = set()
        combined: list[PolicyChunk] = []

        # 1. Deterministic mandatory policies for intent / event
        mandatory = self.retrieve_mandatory(intent=intent, event_type=event_type)
        for chunk in mandatory:
            if chunk.chunk_id not in seen_chunks:
                seen_chunks.add(chunk.chunk_id)
                combined.append(chunk)

        # 2. Semantic search
        semantic = self.search(query, top_k=top_k)
        for chunk in semantic:
            if chunk.chunk_id not in seen_chunks:
                seen_chunks.add(chunk.chunk_id)
                combined.append(chunk)

        return combined

    def as_tool(self) -> dict[str, Any]:
        """Expose policy retrieval as a structured tool definition for LangGraph agents."""
        return {
            "name": "retrieve_banking_policies",
            "description": "Retrieve applicable company banking policies, restrictions, and customer journey rules using semantic search and mandatory compliance filters.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query or question about the banking scenario or rule",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Optional customer intent (e.g. 'issue_card', 'block_card', 'replace_card')",
                    },
                    "event_type": {
                        "type": "string",
                        "description": "Optional triggering event (e.g. 'card_blocked_success', 'card_issuance_requested')",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }
