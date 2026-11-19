"""Real FastEmbed and Qdrant check in a unique temporary collection; no LLM calls."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from uuid import uuid4

from card_chat.policy.factory import build_policy_retriever, build_policy_evaluator
from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline


def main():
    config = {**os.environ, 'EMBEDDING_PROVIDER':'fastembed', 'POLICY_VECTOR_STORE':'qdrant',
              'POLICY_COLLECTION':'geography_verification_' + uuid4().hex}
    if not config.get('QDRANT_URL','').startswith(('http://','https://')):
        raise RuntimeError('Set QDRANT_URL to the local Qdrant service')
    r = build_policy_retriever(config=config)
    try:
        with TemporaryDirectory() as directory:
            PolicyIngestionPipeline(r._doc_source,r._embedder,r._store,
                audit_log_path=Path(directory)/'audit.json').ingest_all()
            r.strict_index = True
            r._store.client.query_points = Mock(wraps=r._store.client.query_points)
            evaluator = build_policy_evaluator(r)
            for code,expected in [('AU','allowed'),('KR','allowed'),('KP','denied')]:
                decision,evidence = evaluator.geographic_decision({'code':code,'status':'explicit'})
                assert decision.decision == expected, decision.model_dump()
                assert any(e.policy_id=='POL-006' and e.mandatory and e.content_hash for e in evidence)
                print(f'PASS {code}: {decision.decision}; POL-006 v{decision.version}; mandatory evidence and hash present')
            assert r._store.client.query_points.call_count > 0
            print(f'PASS real FastEmbed/Qdrant query_points calls: {r._store.client.query_points.call_count}; no hosted model used')
    finally:
        if r._store.client.collection_exists(r._store.collection_name):
            r._store.client.delete_collection(r._store.collection_name)
        r._store.client.close()


if __name__ == '__main__':
    main()
