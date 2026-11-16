"""Explicit isolated RAG dependencies for the existing application regression suite."""
import pytest

from card_chat.policy.factory import build_policy_retriever, build_policy_evaluator
from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline


@pytest.fixture
def policy_retriever(tmp_path):
    retriever = build_policy_retriever(config={
        'EMBEDDING_PROVIDER': 'deterministic', 'POLICY_VECTOR_STORE': 'memory',
    })
    PolicyIngestionPipeline(retriever._doc_source, retriever._embedder, retriever._store,
                            audit_log_path=tmp_path / 'policy-audit.json').ingest_all()
    return retriever


@pytest.fixture(autouse=True)
def isolated_policy_dependencies(request, monkeypatch):
    # Live integration and startup configuration tests exercise the unmodified factory.
    if request.node.get_closest_marker('live_policy'):
        return
    retriever = request.getfixturevalue('policy_retriever')
    from card_chat.policy import factory
    from card_chat.orchestration.supervisor import SupervisorAgent
    runtime_factory = factory.build_runtime_policy_services
    monkeypatch.setattr(factory, 'build_runtime_policy_services',
                        lambda config, *, retriever=None: runtime_factory(
                            config, retriever=retriever or isolated_retriever))
    isolated_retriever = retriever
    original_init = SupervisorAgent.__init__

    def injected_init(self, model, *args, **kwargs):
        if kwargs.get('policy_evaluator') is None:
            kwargs['policy_retriever'] = retriever
            kwargs['policy_evaluator'] = build_policy_evaluator(retriever)
        original_init(self, model, *args, **kwargs)

    monkeypatch.setattr(SupervisorAgent, '__init__', injected_init)
