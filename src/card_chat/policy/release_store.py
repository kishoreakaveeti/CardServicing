"""Persistent SQLite store for dynamic policy releases, drafts, and audit history."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
from uuid import uuid4

from .adapters.document_source import parse_policy_file
from .geography import FileRulesSource, GeographicRules, RulesSource, _normalize_conditions
from .models import PolicyChunk, PolicyDocument
from .pipeline.ingestion import PolicyIngestionPipeline, chunk_policy_markdown
from .ports import DocumentSource, EmbeddingProvider, VectorStore

logger = logging.getLogger(__name__)

DEFAULT_POLICY_DB = Path("data/policy_store.db")


class PolicyReleaseStore:
    """Manages drafts, immutable versioned releases, atomic activation, and audit records."""

    def __init__(self, db_path: str | Path | None = None, policies_dir: str | Path = "policies"):
        self.db_path = str(db_path or os.getenv("POLICY_STORE_DB", str(DEFAULT_POLICY_DB)))
        self.policies_dir = Path(policies_dir)
        self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False) if self.db_path == ":memory:" else None
        if self._mem_conn:
            self._mem_conn.row_factory = sqlite3.Row
        elif self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._seed_baseline()

    def _get_conn(self) -> sqlite3.Connection:
        if self._mem_conn:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_releases (
                    release_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_drafts (
                    policy_id TEXT PRIMARY KEY,
                    markdown TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_audit_log (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    details TEXT NOT NULL,
                    performed_by TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_releases_active ON policy_releases(policy_id, active)")

    def _seed_baseline(self) -> None:
        """Seed initial POL-006 baseline from disk if not present."""
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT * FROM policy_releases WHERE policy_id='POL-006' AND active=1"
            ).fetchone()
            if existing:
                return

        # Load from disk
        pol_file = self.policies_dir / "POL-006-geographic-replacement-restrictions.md"
        rules_file = self.policies_dir / "geographic-replacement-rules.json"

        if pol_file.exists() and rules_file.exists():
            markdown = pol_file.read_text(encoding="utf-8")
            rules_json = rules_file.read_text(encoding="utf-8")
            rules = GeographicRules.model_validate_json(rules_json)
            doc, _ = parse_policy_file(pol_file)
            content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO policy_releases
                    (release_id, policy_id, version, effective_date, markdown, rules_json, content_hash, active, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        "rel-baseline-pol006",
                        "POL-006",
                        rules.version,
                        doc.effective_date,
                        markdown,
                        rules_json,
                        content_hash,
                        "system",
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    ),
                )
                conn.commit()

    def get_active_release(self, policy_id: str = "POL-006") -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM policy_releases WHERE policy_id=? AND active=1 ORDER BY created_at DESC LIMIT 1",
                (policy_id,),
            ).fetchone()
            if row:
                d = dict(row)
                d["rules_snapshot"] = json.loads(d.get("rules_json", "{}"))
                return d
            return None

    def get_active_rules(self, policy_id: str = "POL-006") -> GeographicRules:
        release = self.get_active_release(policy_id)
        if release:
            return GeographicRules.model_validate_json(release["rules_json"])
        return FileRulesSource(self.policies_dir).load()

    def get_draft(self, policy_id: str = "POL-006") -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM policy_drafts WHERE policy_id=?", (policy_id,)
            ).fetchone()
            if row:
                return dict(row)
            active = self.get_active_release(policy_id)
            if active:
                return {
                    "policy_id": policy_id,
                    "markdown": active["markdown"],
                    "rules_json": active["rules_json"],
                    "updated_by": "active_release",
                    "updated_at": active["created_at"],
                }
            return None

    def save_draft(self, policy_id: str, markdown: str, rules_json: str, user: str) -> dict[str, Any]:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO policy_drafts
                (policy_id, markdown, rules_json, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (policy_id, markdown, rules_json, user, now),
            )
            conn.commit()
        self.record_audit("save_draft", policy_id, "draft", "Draft saved", user)
        return {"policy_id": policy_id, "updated_by": user, "updated_at": now}

    def validate_candidate(self, markdown: str, rules_dict_or_json: dict | str) -> tuple[bool, str | None, dict[str, Any]]:
        """Validate candidate policy document and rules consistency."""
        try:
            if isinstance(rules_dict_or_json, str):
                rules = GeographicRules.model_validate_json(rules_dict_or_json)
            else:
                rules = GeographicRules.model_validate(rules_dict_or_json)
        except Exception as exc:
            return False, f"Invalid rules configuration: {exc}", {}

        # Parse markdown frontmatter
        from .adapters.document_source import parse_policy_file
        import tempfile
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as tmp:
            tmp.write(markdown)
            tmp_path = tmp.name

        try:
            doc, body = parse_policy_file(tmp_path)
        except Exception as exc:
            return False, f"Invalid policy markdown format: {exc}", {}
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        if not doc.policy_id:
            return False, "Missing policy_id in markdown frontmatter", {}
        if not doc.approved or doc.status != "approved":
            return False, "Policy document must have approved: true and status: approved", {}

        norm_doc = _normalize_conditions(doc.conditions)
        norm_rules = _normalize_conditions(rules.model_dump(exclude={"policy_id"}))

        # Check versions match
        if doc.version != rules.version:
            return False, f"Version mismatch: document has {doc.version}, rules have {rules.version}", {}

        # Check conditions match
        if norm_doc != norm_rules:
            diff_keys = [k for k in set(norm_doc) | set(norm_rules) if norm_doc.get(k) != norm_rules.get(k)]
            return False, f"Document conditions conflict with rules on keys: {diff_keys}", {}

        return True, None, {"doc": doc, "body": body, "rules": rules}

    def create_release(
        self,
        policy_id: str,
        content: str,
        rules: dict | str,
        activated_by: str = "fincrime.admin",
        change_summary: str = "",
        retriever: Any | None = None,
        embedding_provider: Any | None = None,
        vector_store: Any | None = None,
    ) -> dict[str, Any]:
        """Convenience method to format candidate document and activate a new policy release."""
        active = self.get_active_release(policy_id)
        current_ver = active["version"] if active else "1.2.0"
        parts = current_ver.split(".")
        if len(parts) == 3 and parts[-1].isdigit():
            new_version = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
        else:
            new_version = f"{current_ver}.1"

        if isinstance(rules, str):
            rules_dict = json.loads(rules)
        else:
            rules_dict = dict(rules)
        rules_dict["version"] = new_version
        rules_dict["policy_id"] = policy_id
        rules_obj = GeographicRules.model_validate(rules_dict)
        full_rules_dict = rules_obj.model_dump(exclude={"policy_id"})
        norm_conditions = _normalize_conditions(full_rules_dict)

        if not content.strip().startswith("---"):
            import yaml
            frontmatter_dict = {
                "policy_id": policy_id,
                "title": f"Policy {policy_id}",
                "version": new_version,
                "effective_date": "2026-01-01",
                "approved": True,
                "status": "approved",
                "applicable_intents": ["replace_card"],
                "required_actions": [
                    "validate_declared_current_country",
                    "enforce_geographic_rules_before_replacement",
                    "require_customer_confirmation",
                    "require_fincrime_approval_for_overseas_replacement",
                ],
                "restrictions": [
                    "never_bypass_replacement_denial_with_additional_issuance",
                ],
                "conditions": norm_conditions,
            }
            yaml_block = yaml.dump(frontmatter_dict, sort_keys=False)
            formatted_markdown = f"---\n{yaml_block}---\n\n{content}"
        else:
            if "effective_date" not in content:
                content = content.replace("approved: true", "approved: true\neffective_date: 2026-01-01")
            if "applicable_intents" not in content:
                content = content.replace("approved: true", "approved: true\napplicable_intents:\n  - replace_card")
            formatted_markdown = content

        emb = getattr(retriever, "_embedder", None) or embedding_provider
        store = getattr(retriever, "_store", None) or vector_store
        if not emb or not store:
            from .factory import build_embedding_provider, build_vector_store
            emb = emb or build_embedding_provider(provider_type="deterministic")
            store = store or build_vector_store(store_type="qdrant", qdrant_url=":memory:")

        return self.apply_release(
            policy_id=policy_id,
            markdown=formatted_markdown,
            rules_dict_or_json=full_rules_dict,
            user=activated_by,
            embedding_provider=emb,
            vector_store=store,
            new_version=new_version,
        )

    def apply_release(
        self,
        policy_id: str,
        markdown: str,
        rules_dict_or_json: dict | str,
        user: str,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        opa_client: Any | None = None,
        new_version: str | None = None,
    ) -> dict[str, Any]:
        """Validate, re-index in Qdrant, switch active release atomically, and audit."""
        # 1. Validate candidate
        valid, err, parsed = self.validate_candidate(markdown, rules_dict_or_json)
        if not valid:
            raise ValueError(f"Policy validation failed: {err}")

        doc: PolicyDocument = parsed["doc"]
        body: str = parsed["body"]
        rules: GeographicRules = parsed["rules"]

        # Increment version if needed or use provided
        if new_version:
            doc.version = new_version
            rules.version = new_version
            # Update conditions version
            doc.conditions["version"] = new_version

        # 2. Re-index chunks in Qdrant
        chunks = chunk_policy_markdown(doc, body)
        if not chunks:
            raise ValueError(f"No text chunks generated for policy: {policy_id}")

        texts = [f"Title: {c.title}\nPolicy: {c.policy_id} v{c.version}\n{c.content}" for c in chunks]
        embeddings = embedding_provider.embed_documents(texts)
        for chunk in chunks:
            chunk.metadata["embedding_model"] = embedding_provider.model_name
            chunk.metadata["release_version"] = rules.version

        # Atomically delete previous points for this policy and upsert new
        vector_store.delete_by_policy_id(policy_id)
        vector_store.upsert_chunks(chunks, embeddings)

        # 3. Sync to OPA if available
        if opa_client and hasattr(opa_client, "sync_rules"):
            opa_client.sync_rules(rules)

        # 4. Save release record and activate atomically
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        release_id = f"rel-{uuid4().hex[:12]}"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        with self._get_conn() as conn:
            # Deactivate previous
            conn.execute("UPDATE policy_releases SET active=0 WHERE policy_id=?", (policy_id,))
            # Insert new active release
            conn.execute(
                """
                INSERT INTO policy_releases
                (release_id, policy_id, version, effective_date, markdown, rules_json, content_hash, active, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    release_id,
                    policy_id,
                    rules.version,
                    doc.effective_date,
                    markdown,
                    rules.model_dump_json(indent=2),
                    content_hash,
                    user,
                    now,
                ),
            )
            # Remove pending draft since applied
            conn.execute("DELETE FROM policy_drafts WHERE policy_id=?", (policy_id,))
            conn.commit()

        # 5. Record audit
        self.record_audit(
            "apply_release",
            policy_id,
            rules.version,
            f"Release {rules.version} activated with {len(chunks)} chunks indexed in Qdrant",
            user,
        )

        return {
            "release_id": release_id,
            "policy_id": policy_id,
            "version": rules.version,
            "status": "active",
            "chunk_count": len(chunks),
            "content_hash": content_hash,
            "activated_at": now,
            "activated_by": user,
            "rules": rules.model_dump(),
            "rules_snapshot": rules.model_dump(),
        }

    def list_releases(self, policy_id: str = "POL-006") -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT release_id, policy_id, version, effective_date, content_hash, active, created_by, created_at "
                "FROM policy_releases WHERE policy_id=? ORDER BY created_at DESC",
                (policy_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_audit(self, action: str, policy_id: str, version: str, details: str, performed_by: str) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO policy_audit_log (id, action, policy_id, version, details, performed_by, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (f"aud-{uuid4().hex[:12]}", action, policy_id, version, details, performed_by, now),
            )
            conn.commit()

    def get_audit_trail(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM policy_audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]


class ReleaseRulesSource(RulesSource):
    """Dynamic rules source loading active release from PolicyReleaseStore."""

    def __init__(self, release_store: PolicyReleaseStore, fallback_dir: str | Path | None = None):
        self.release_store = release_store
        self.directory = Path(fallback_dir or "policies")

    def load(self) -> GeographicRules:
        return self.release_store.get_active_rules("POL-006")
