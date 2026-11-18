# Azure VM Compose preparation

Use `compose.azure.yaml` as a **standalone** Compose file, not an override of
`compose.yaml`. Local launch and optional Mongo telemetry retain their existing
configuration. The same Dockerfiles build both deployments; no Azure imports
enter banking logic, MCP tools, policy enforcement or LangGraph workflows.

## Deployment configuration

1. Copy `.env.azure.example` to ignored `.env.azure`, replace hostnames and secrets,
   and restrict permissions to the deployment operator. Pick an Anthropic model
   available to your account. Keep the public application and identity origins
   on HTTPS. The BFF derives its callback as `PUBLIC_ORIGIN/auth/callback`.
2. Generate fresh identity artifacts using `scripts/setup_stack.py` with
   `PUBLIC_ORIGIN` and `KEYCLOAK_SSL_REQUIRED=external` in its process environment.
   It preserves existing `.env.stack` credentials and signing keys. Copy the
   generated service/admin credentials into `.env.azure` exactly; blank values
   in the example are not usable credentials. Do not print or commit these files.
   The script does not read `.env.azure` automatically. For example, before setup:

   ```powershell
   $env:PUBLIC_ORIGIN = 'https://bank.example.com'
   $env:KEYCLOAK_SSL_REQUIRED = 'external'
   python scripts/setup_stack.py
   ```

   Mount `IDENTITY_CONFIG_DIR` containing `realm.json`, `delegation-public.pem`,
   and `delegation-private.pem`; only the BFF receives the private key. On Linux
   the UID 10001 application user must be able to read its mounted keys. Keep
   private keys restricted to that identity. Never regenerate keys on restart.
   Keycloak imports realms only on first creation: changing the generated JSON
   does not update an existing realm; reconcile callback/SSL/client settings
   explicitly through its authenticated administration API before reusing data.
3. Pre-create the private Blob container. Grant the VM managed identity
   **Storage Blob Data Contributor** scoped to that container. Leave the connection
   string blank for managed identity; optionally set `AZURE_CLIENT_ID` for a user
   assigned identity. VM containers must reach IMDS and the storage HTTPS endpoint.
   Alternatively inject `AZURE_STORAGE_CONNECTION_STRING` securely at runtime.
   Configure storage retention/lifecycle separately; Blob has no Mongo TTL index.
4. Configure a trusted host reverse proxy with HTTPS certificates to forward the
   application hostname to `127.0.0.1:8080` and identity hostname to
   `127.0.0.1:8081`, preserving Host and overwriting X-Forwarded-* headers. Keycloak
   uses production `start`, HTTP only on the private backend, public HTTPS hostname
   and `xforwarded` proxy headers. Do not expose backend ports on the VM firewall.
   This step does not install a proxy, request certificates or provision resources.
5. Validate, build, ingest policies, then start:

   ```powershell
   docker compose --env-file .env.azure -f compose.azure.yaml config --quiet
   docker compose --env-file .env.azure -f compose.azure.yaml build
   docker compose --env-file .env.azure -f compose.azure.yaml up -d qdrant identity
   docker compose --env-file .env.azure -f compose.azure.yaml run --rm --no-deps --workdir /data --entrypoint python cards-agent -m card_chat.policy.pipeline.cli ingest
   docker compose --env-file .env.azure -f compose.azure.yaml up -d
   docker compose --env-file .env.azure -f compose.azure.yaml ps
   ```

   Wait for Qdrant readiness before ingestion. Ingestion and runtime must select
   the same policy directory, embedding model and collection. Missing/stale index
   deliberately fails readiness; no security or RAG bypass is added. See
   [live policy runtime](live-policy-runtime.md) for workflow verification.

Only frontend and identity publish ports, both on loopback for the host proxy.
Cards API, FinCrime API, the two agents, MCP and BFF remain separate containers.
No Mongo, RabbitMQ or Redis is started. SQLite data volumes have distinct owners;
no service reads another service's database file. Qdrant has its own volume.
Named volumes are configurable with `*_VOLUME`; back them up and place Docker's
volume storage on the persistent VM disk. Never use `down --volumes` on retained
data. SQLite path overrides must stay inside that service's mounted `/data`.
`QDRANT_URL` configures the remote vector endpoint; `QDRANT_VOLUME` controls storage.

Internal service URLs, public issuer, JWKS, token and authorization endpoints are
configurable independently. The public issuer must match minted tokens exactly;
using a private token/JWKS URL does not disable issuer, audience or signature checks.
Frontend assets call same-origin paths; `EXPERIENCE_UPSTREAM` controls the reverse
proxy destination. `DNS_RESOLVER` defaults to Docker DNS; configure the platform
resolver when deploying the same frontend image outside Docker Compose. Nginx
re-resolves its upstream so BFF container recreation does not require a rebuild.

Startup probes (overridable with the per-service `*_STARTUP_CHECK_URLS` settings)
retry dependency reads for at most 60 seconds, then exit and let
`restart: unless-stopped` restart the process. They never replay banking writes.
Existing HTTP health checks and ASGI shutdown hooks remain. Missing credentials,
invalid MCP catalogue or unapproved policy indexes still fail closed. HTTP health
alone is not an end-to-end dependency check; verify actual workflows after launch.

## Telemetry providers

`TELEMETRY_SINK=mongo|azure_blob|stdout|none` uses the existing telemetry port.
Mongo retains `MONGODB_HOST`, `MONGODB_PORT`, `MONGODB_DATABASE`, `MONGO_USER`,
`MONGO_PASSWORD` and its existing TTL indexes. `stdout` emits allowlisted JSON;
`none` disables diagnostics. Azure uses `AZURE_STORAGE_ACCOUNT_URL`,
`AZURE_STORAGE_CONTAINER` (default `telemetry`), optional
`AZURE_STORAGE_CONNECTION_STRING` and `AZURE_CLIENT_ID`. Hashing still uses
`ACCOUNT_HASH_KEY` and `ACCOUNT_HASH_KEY_VERSION`; absent safe hashing omits accounts.

Blob export preserves `_id`, UTC timestamp, kind/event and existing allowlisted
fields including service and trace/span correlation. HTTP events supply their
existing correlation IDs; startup/non-request events need not have a trace ID.
No payloads, prompts, tokens, account numbers or exception messages are exported.
Records also appear as structured stdout for operational diagnosis.
The worker batches up to 100 events or one second, queues at most 1000 events,
and tries each immutable NDJSON blob up to three times with exponential backoff.
Object paths use UTC partitions and random UUIDs, never customer input; retries
reuse the object name and never overwrite. Network timeouts and SDK retries are
bounded. Overflow/outage loses diagnostics rather than blocking transactions;
stdout and redacted failure metadata remain available. Shutdown waits at most
five seconds; queued events can be lost on shutdown/crash. This is not the durable
banking audit ledger. A future S3 adapter can implement the same port without
changing application workflows.

## Future Kubernetes / scaling constraints

- Keep one replica per SQLite-owning service. SQLite serializes writes and its
  volumes need durable single-writer placement. Moving to another node requires
  reattaching storage; independent replicas cannot share host SQLite files.
- Agent conversation objects, asyncio locks, task sets and PII token vault remain
  in memory. Persisted journals/checkpoints do not restore every live conversation
  or private token binding after a restart. Active sessions may need restarting.
  Horizontal scaling needs a deliberate session/state/locking design.
- The BFF session DB and agent checkpointer are service-owned local persistence.
  The Cards API's legacy item store is process-local. No cross-service files are
  introduced; keys and approved policies are read-only deployment artifacts.
- Keycloak retains its embedded development-file database for this single-node
  demo, even with production `start`. It is not an HA identity deployment; a
  supported external identity database will be needed for multiple replicas.
- Single Qdrant and VM-local volumes are not HA. Coordinate ingestion and runtime
  policy versions and back up Qdrant snapshots separately.
- Compose startup ordering does not carry to Kubernetes. Reuse health endpoints,
  configure platform probes/restarts and DNS/URLs; validate platform identity and
  storage access. Blob export is best effort, not a durable queue or audit store.

## Verification and remaining deployment gates

Regression and mocked-provider results are recorded in the delivery report.
YAML/service-boundary checks are automated. Docker CLI/engine is unavailable on
this workstation: actual `docker compose config`, Linux builds, container startup,
live Mongo, HTTPS Keycloak login/delegation, live Blob/managed identity and real
Qdrant/FastEmbed integration remain deployment gates. No Azure resources were
provisioned. Supply DNS/TLS/proxy, secrets/realm/key permissions, persistent storage,
Blob container/RBAC, policy ingestion, then run the existing smoke workflows
(`scripts/smoke_stack.py`, `scripts/smoke_block_replace.py`) against the deployment.

SDK retry and identity configuration follows Microsoft's
[Blob retry documentation](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-retry-policy-python)
and [upload/authorization documentation](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python).

## Implemented files and recorded test results

Modified:

- `.gitignore`: ignores populated Azure environment file.
- `README.md`: deployment guide link.
- `pyproject.toml`: Azure Storage and Identity dependencies shared by app builds.
- `frontend/Dockerfile`, `frontend/nginx.conf`: runtime-configured upstream and DNS refresh.
- `scripts/service_entrypoint.py`: bounded dependency probes before ASGI startup.
- `scripts/setup_stack.py`: configurable browser callback origin and realm SSL policy.
- `src/card_servicing/config.py`: provider selection and safe Blob configuration.
- `src/card_servicing/main.py`: Blob adapter selection for Cards API.
- `src/card_servicing/telemetry.py`: queued, batched Blob implementation.
- `src/card_chat/observability.py`: Blob selection for instrumented services.

Created:

- `compose.azure.yaml`: standalone nine-container Azure deployment configuration.
- `.env.azure.example`: credential-free configuration template.
- `src/card_servicing/startup.py`: bounded, read-only dependency probes.
- `tests/test_blob_telemetry.py`: selection, validation, batching, redaction, queue
  overflow, retries, ambiguous upload success, client failure and SQLite replay.
- `tests/test_cloud_startup.py`: transient failure and startup deadline checks.
- `tests/test_cloud_compose.py`: YAML, service boundaries, health checks, private
  ports, separate data volumes and unchanged local Mongo configuration.
- `docs/azure-compose.md`: deployment procedure, constraints and this report.

| Status | Verification |
| --- | --- |
| PASS | Full regression suite: **297 passed, 1 skipped** after initial implementation. Covers existing SQLite, auth/delegation, MCP, orchestration, privacy and policy tests. |
| PASS | Final targeted suite after transport/worker hardening: **42 passed** across Blob, startup, Compose, existing startup configuration and service observability tests. Includes one additional HTTPS connection-string test. |
| PASS | Mocked Blob outages preserve successful SQLite writes and idempotent replay; existing mocked Mongo outage and selection tests pass. |
| PASS | Azure and local Compose YAML parse and structural checks; `git diff --check`. |
| FAIL | No test failures in completed runs. |
| NOT TESTED | Docker Compose CLI validation, Linux image builds and local/Azure container end-to-end runs: Docker CLI/engine unavailable. |
| NOT TESTED | Real Mongo and Azure Blob/managed identity operations, reverse proxy/TLS and live Keycloak browser login. |
| NOT TESTED | Live external Qdrant/FastEmbed integration: one test skipped because `POLICY_TEST_QDRANT_URL` is unset. Embedded/mocked regression coverage passed. |

The full suite preceded the last credential/stdout adjustments; those affected
areas were rerun in the final 42-test selection. No security checks were weakened.
