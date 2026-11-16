"""DocumentSource adapters for reading and validating policy documents."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any
import yaml

from ..models import PolicyDocument
from ..ports import DocumentSource

logger = logging.getLogger(__name__)


def parse_policy_file(file_path: Path | str) -> tuple[PolicyDocument, str]:
    """Parse a versioned Markdown policy file with YAML frontmatter."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    parts = raw_text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Invalid policy markdown format in {path.name}: missing YAML frontmatter delimiters '---'")

    frontmatter_raw = parts[1].strip()
    body_markdown = parts[2].strip()

    try:
        meta: dict[str, Any] = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML frontmatter in {path.name}: {exc}") from exc

    required_keys = ["policy_id", "version", "effective_date", "title"]
    for key in required_keys:
        if key not in meta or not meta[key]:
            raise ValueError(f"Policy in {path.name} is missing mandatory metadata attribute: '{key}'")

    status = str(meta.get("status", "approved")).lower()
    approved = bool(meta.get("approved", status == "approved"))

    doc = PolicyDocument(
        policy_id=str(meta["policy_id"]).strip().upper(),
        version=str(meta["version"]).strip(),
        effective_date=str(meta["effective_date"]).strip(),
        title=str(meta["title"]).strip(),
        status=status,
        approved=approved,
        applicable_intents=[str(i).strip() for i in meta.get("applicable_intents", [])],
        triggering_events=[str(e).strip() for e in meta.get("triggering_events", [])],
        conditions=dict(meta.get("conditions", {})),
        required_actions=[str(a).strip() for a in meta.get("required_actions", [])],
        restrictions=[str(r).strip() for r in meta.get("restrictions", [])],
        source=str(meta.get("source", str(path.as_posix()))),
        raw_content=raw_text,
        content_hash=content_hash,
    )
    return doc, body_markdown


class FileSystemDocumentSource(DocumentSource):
    """Loads and validates policy markdown documents from a local filesystem directory."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def list_documents(self, approved_only: bool = True) -> list[PolicyDocument]:
        """List all valid policy documents found in the configured directory."""
        if not self.directory.exists() or not self.directory.is_dir():
            logger.warning("Policy directory does not exist: %s", self.directory)
            return []

        documents: list[PolicyDocument] = []
        for file_path in sorted(self.directory.glob("*.md")):
            try:
                doc, _ = parse_policy_file(file_path)
                if approved_only and (not doc.approved or doc.status != 'approved'):
                    logger.debug("Skipping unapproved policy %s (status=%s)", doc.policy_id, doc.status)
                    continue
                documents.append(doc)
            except Exception as exc:
                logger.error("Failed to parse policy file %s: %s", file_path.name, exc)
        return documents

    def get_document(self, policy_id: str, version: str | None = None) -> PolicyDocument | None:
        """Find a specific policy document by ID and optional version."""
        norm_id = policy_id.strip().upper()
        for doc in self.list_documents(approved_only=False):
            if doc.policy_id == norm_id:
                if version is None or doc.version == version:
                    return doc
        return None


class InMemoryDocumentSource(DocumentSource):
    """Test double holding policy documents in memory."""

    def __init__(self, documents: list[PolicyDocument] | None = None):
        self._documents: dict[tuple[str, str], PolicyDocument] = {}
        if documents:
            for d in documents:
                self._documents[(d.policy_id, d.version)] = d

    def add_document(self, doc: PolicyDocument) -> None:
        self._documents[(doc.policy_id, doc.version)] = doc

    def list_documents(self, approved_only: bool = True) -> list[PolicyDocument]:
        return [
            d for d in self._documents.values()
            if not approved_only or (d.approved and d.status == 'approved')
        ]

    def get_document(self, policy_id: str, version: str | None = None) -> PolicyDocument | None:
        norm_id = policy_id.strip().upper()
        matches = [d for (pid, v), d in self._documents.items() if pid == norm_id]
        if not matches:
            return None
        if version:
            for d in matches:
                if d.version == version:
                    return d
            return None
        # Return newest / first
        return matches[-1]
