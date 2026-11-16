# Live policy runtime

The FastAPI lifespan composes one `HybridPolicyRetriever` and one
`GroundedPolicyEvaluator` through `card_chat.policy.factory`. The orchestrator
passes these same instances to its existing LangGraph supervisor. The graph's
nodes and durable confirmation/resume protocol are unchanged.

The previous runtime path was `main → SQLite PolicyStore → legacy PolicyEvaluator`;
the supervisor also constructed a store when none was supplied. The new path is
`FastAPI → AgentOrchestrator → SupervisorAgent → GroundedPolicyEvaluator →
HybridPolicyRetriever → EmbeddingProvider / VectorStore`. In deployment these
providers are FastEmbed and the persistent Qdrant service. Approved document
metadata supplies mandatory rules; each mandatory version must also exist in the
vector index with the matching content hash and embedding model. Semantic hits
from other versions or unapproved documents are excluded.

The BFF's delegated card summaries omit block reasons. The trusted gateway reads
the customer-scoped `policy-facts://accounts/...` MCP resource to enrich these
facts from existing FinCrime read APIs. The MCP service forwards its existing
service credentials and customer delegation, checks account ownership and tool
permission, and reads only card IDs present in the signed customer assertion.
These facts enter trusted graph state; model context still uses its private-field
projection. The customer tool catalogue and banking API restrictions are unchanged.

For an explicit request to block a card and reissue/replace it, the application
shows one combined review. Confirming that review authorizes both writes for the
same card. Blocking must succeed before replacement runs; the replacement is
policy-checked again, persisted before execution, and uses its own idempotency
key. A block-only request still uses the existing optional replacement offer.
Unblocking and additional issuance retain separate confirmations.

The MCP service account needs both the `cards` and `fincrime` audiences and the
`cards:write` and `tools:execute` scopes. Fresh setup generates these grants. For
an existing local realm, run `python scripts/sync_identity_scopes.py` to reconcile
them without changing credentials. `python scripts/smoke_block_replace.py` checks
the deployed workflow with a new synthetic customer, actual card states, one
combined confirmation and an idempotent replay.

Configure `QDRANT_URL`, `EMBEDDING_PROVIDER=fastembed`,
`POLICY_VECTOR_STORE=qdrant`, `POLICIES_DIR`, and optionally `FASTEMBED_MODEL`
and `POLICY_COLLECTION` (default `banking_policies`). The ingestion process and
runtime must use the same values. Docker already sets the persistent service URL
to `http://qdrant:6333` and mounts the approved policy directory.

Ingest independently before starting the agent, and after approved policy changes:

```powershell
docker compose --env-file .env --env-file .env.stack up -d qdrant
docker compose --env-file .env --env-file .env.stack build cards-agent
docker compose --env-file .env --env-file .env.stack run --rm --no-deps --workdir /data --entrypoint python cards-agent -m card_chat.policy.pipeline.cli ingest
docker compose --env-file .env --env-file .env.stack up -d
```

For native launch, set `QDRANT_URL=http://127.0.0.1:6333` and run
`python -m card_chat.policy.pipeline.cli ingest` with the same configuration.
Indexes created before embedding-model metadata was recorded need ingestion once.
Docker stores the ingestion audit log at `POLICY_AUDIT_LOG=/data/policy_audit.json`
in the agent volume. The writable working directory in the command also supports
older images whose audit log uses a relative `data/` directory.
Use the same environment files and Compose overrides as the normal stack launcher
so ingestion and runtime select the same embedding model and collection. For debug
containers, include `-f compose.yaml -f compose.debug.yaml` when recreating services.
Startup only reads the index; it never creates collections or reindexes policies.
Missing infrastructure, missing policies, stale content, ambiguous approved
versions, or incompatible embeddings cause startup to fail. Retrieval failure
during a workflow prevents restricted delegation and API execution. Approved
version changes during a pinned workflow require a new workflow.

`tests/test_live_policy_runtime.py` exercises real HTTP requests, persisted graph
decisions and citations, unblocking offers and acceptance, replacement after actual
successful blocking, vector failure, and confirmation-time revalidation. The
ordinary tests use embedded Qdrant and deterministic embeddings, and spy on
`QdrantClient.query_points` after startup to prove interaction-time retrieval.

Run the Docker-backed FastEmbed variant against an available Qdrant service:

```powershell
$env:POLICY_TEST_QDRANT_URL = 'http://127.0.0.1:6333'
python -m pytest tests/test_live_policy_runtime.py -q
```

That test uses a unique collection, independently ingests it, processes an HTTP
request, then deletes only its test collection. It skips when the URL is unset;
when set, connection or model errors fail the test.

The SQLite policy modules remain for their existing legacy regression tests.
They have no imports or constructors in the live composition root,
orchestrator or supervisor. Existing regression tests explicitly inject isolated
RAG services through test fixtures; live integration tests bypass those overrides.

## Changed files

| Area | Files |
| --- | --- |
| Live composition and orchestration | `src/card_chat/main.py`, `src/card_chat/agent.py`, `src/card_chat/gateway.py`, `src/card_chat/mcp_server.py`, `src/card_chat/orchestration/supervisor.py`, `src/card_chat/orchestration/graph.py`, `src/card_chat/orchestration/specialists.py` |
| Policy services and provider alignment | `src/card_chat/policy/factory.py`, `src/card_chat/policy/ports.py`, `src/card_chat/policy/adapters/evaluator.py`, `src/card_chat/policy/adapters/retriever.py`, `src/card_chat/policy/adapters/document_source.py`, `src/card_chat/policy/adapters/embeddings/fastembed_provider.py`, `src/card_chat/policy/adapters/vector_stores/memory_store.py`, `src/card_chat/policy/pipeline/ingestion.py` |
| Configuration and launch | `.env.example`, `compose.yaml`, `scripts/local_service.py` |
| Tests | `pytest.ini`, `tests/conftest.py`, `tests/test_live_policy_runtime.py`, `tests/test_fincrime.py`, `tests/test_multi_agent_orchestration.py`, `tests/test_policy_orchestration.py` |
| Documentation | `README.md`, `docs/live-policy-runtime.md` |
