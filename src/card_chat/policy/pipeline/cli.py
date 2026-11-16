"""CLI for enterprise policy ingestion, updates, rollback, and inspection."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from ..factory import build_policy_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("card_chat.policy.cli")


def main(argv: list[str] | None = None) -> int:
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--policies-dir",
        default=os.getenv("POLICIES_DIR", "policies"),
        help="Directory containing versioned markdown policy files",
    )
    parent_parser.add_argument(
        "--vector-store",
        default=os.getenv("POLICY_VECTOR_STORE", "qdrant"),
        choices=["qdrant", "memory"],
        help="Vector store provider adapter",
    )
    parent_parser.add_argument(
        "--qdrant-url",
        default=os.getenv("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant service URL (or ':memory:' / local file path)",
    )
    parent_parser.add_argument(
        "--embedding-provider",
        default=os.getenv("EMBEDDING_PROVIDER", "fastembed"),
        choices=["fastembed", "deterministic"],
        help="Embedding provider adapter",
    )

    parser = argparse.ArgumentParser(
        description="CardServicing Enterprise Policy RAG Ingestion Pipeline CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        parents=[parent_parser],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. init
    subparsers.add_parser("init", parents=[parent_parser], help="Initialize vector store collection and verify embeddings")

    # 2. ingest
    ingest_p = subparsers.add_parser("ingest", parents=[parent_parser], help="Ingest all approved policy documents into the vector store")
    ingest_p.add_argument("--force", action="store_true", help="Force reindexing of all documents")

    # 3. update
    update_p = subparsers.add_parser("update", parents=[parent_parser], help="Update and reindex a specific policy markdown file")
    update_p.add_argument("file", help="Path to markdown policy file to update")
    update_p.add_argument("--force", action="store_true", help="Force update even if hash unchanged")

    # 4. inspect
    subparsers.add_parser("inspect", parents=[parent_parser], help="Inspect indexed policies, chunk counts, and collection health")

    # 5. rollback
    rollback_p = subparsers.add_parser("rollback", parents=[parent_parser], help="Rollback a policy to a previously approved version")
    rollback_p.add_argument("policy_id", help="Policy ID to rollback (e.g. POL-001)")
    rollback_p.add_argument("version", help="Target approved version string (e.g. 1.0.0)")

    args = parser.parse_args(argv)

    pipeline = build_policy_pipeline(
        policies_dir=args.policies_dir,
        vector_store_type=args.vector_store,
        qdrant_url=args.qdrant_url,
        embedding_provider_type=args.embedding_provider,
    )

    if args.command == "init":
        logger.info("Initializing vector store collection...")
        pipeline.initialize()
        logger.info("Vector store initialization successful.")
        return 0

    if args.command == "ingest":
        logger.info("Starting ingestion of approved policies from: %s", args.policies_dir)
        results = pipeline.ingest_all(force=args.force)
        print("\n=== Ingestion Summary ===")
        for pid, count in results.items():
            print(f"  {pid}: {count} chunks indexed")
        print(f"Total policies indexed: {len(results)}\n")
        return 0

    if args.command == "update":
        path = Path(args.file)
        if not path.exists():
            logger.error("File does not exist: %s", path)
            return 1
        logger.info("Updating policy from: %s", path)
        count = pipeline.ingest_file(path, force=args.force)
        print(f"Successfully updated {path.name}: {count} chunks indexed.")
        return 0

    if args.command == "inspect":
        info = pipeline.inspect()
        print("\n=== Policy Store Inspection ===")
        print(f"Vector Store Vectors: {info['total_vectors']}")
        print(f"Embedding Model:     {info['embedding_model']} (dim={info['embedding_dimension']})")
        print("\nDocuments:")
        for doc in info["available_documents"]:
            status_tag = f"[{doc['status'].upper()}]" if doc["approved"] else f"[UNAPPROVED: {doc['status']}]"
            print(f"  - {doc['policy_id']} v{doc['version']}: {doc['title']} {status_tag} (eff: {doc['effective_date']})")
        print(f"\nAudit Log Entries:    {info['audit_trail_count']}\n")
        return 0

    if args.command == "rollback":
        logger.info("Rolling back policy %s to version %s...", args.policy_id, args.version)
        # Find document matching target version
        doc = pipeline.source.get_document(args.policy_id, version=args.version)
        if not doc:
            logger.error("No document found for %s v%s", args.policy_id, args.version)
            return 1
        body = doc.raw_content
        parts = body.split("---", 2)
        body_md = parts[2].strip() if len(parts) >= 3 else body
        count = pipeline.ingest_document(doc, body_md, force=True)
        print(f"Rollback complete: {args.policy_id} restored to v{args.version} ({count} chunks).")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
