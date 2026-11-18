# Azure VM Docker Compose

The automated deployment and lifecycle commands are in
[infra/azure/README.md](../infra/azure/README.md). Start there for prerequisites,
owned DNS names, Azure permissions, secrets, HTTPS, backups and exact commands.
Nothing is provisioned until you explicitly run `infra/azure/deploy.ps1`.

`compose.azure.yaml` is standalone; do not merge it with `compose.yaml`.
The local stack and optional Mongo telemetry remain available through the usual
`scripts/stack.ps1` commands. Both configurations build the same Dockerfiles.

The Azure profile retains Cards API, FinCrime API, Cards Agent, FinCrime Agent,
MCP, Experience BFF, frontend, Qdrant and Keycloak as independent containers.
Keycloak now uses a private **MariaDB 11.4** container and service-owned volume:
embedded H2 can start but is not supported by Keycloak for production mode.
This is a necessary identity storage correction, not a migration of the banking
SQLite databases or an Azure managed database. Existing local Keycloak stays on
its development configuration. Never blindly delete an old identity volume;
manual H2 deployments with identity data need an explicit export/import plan.

Caddy is configured separately by `infra/azure/compose.proxy.yaml`. Only HTTPS
and source-restricted SSH are exposed publicly. Frontend and Keycloak publish
loopback backend ports; all banking APIs, agents, MCP, Qdrant and MariaDB remain
private. Public HTTPS issuer, callback origin, JWKS/token URLs and proxy headers
are kept distinct. The public issuer must match tokens exactly.

## Configuration

`.env.azure.example` documents the standalone Compose settings. Automated Azure
bootstrap generates its own root-only `/opt/cardservicing/state/.env.azure` from
the deployment JSON and secure API-key file. It does not reuse local credentials.
The deployment persists each SQLite database in its owning service's named volume,
Qdrant in `QDRANT_VOLUME`, and identity state in separate Keycloak/MariaDB volumes.
Override database paths only within the corresponding service's mounted `/data`.

`TELEMETRY_SINK` supports `mongo`, `azure_blob`, `stdout`, and `none`.

| Provider | Settings |
| --- | --- |
| mongo | `MONGODB_HOST`, `MONGODB_PORT`, `MONGODB_DATABASE`, `MONGO_USER`, `MONGO_PASSWORD` |
| azure_blob | `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_STORAGE_CONTAINER`, optional `AZURE_CLIENT_ID` or securely injected `AZURE_STORAGE_CONNECTION_STRING` |
| stdout | Allowlisted structured JSON on console |
| none | Disable best-effort diagnostics |

Azure automation enforces `azure_blob` and system-assigned VM identity; it deploys
no MongoDB, RabbitMQ or Redis. Blob objects are bounded batches of NDJSON with
UTC timestamps, service, event, correlation fields and unique immutable names.
Queueing/retries are bounded and remote I/O runs on a worker. Overflow/outages
lose diagnostics rather than block banking writes. Console diagnostics remain
available. Shutdown/crashes can lose queued events; this is not the durable audit
ledger. Configure storage retention separately; Mongo TTL indexes do not apply.
Account hashing continues to use `ACCOUNT_HASH_KEY` and `ACCOUNT_HASH_KEY_VERSION`.

Internal service endpoints and startup probe URLs are configurable. Missing
credentials, invalid MCP contracts, unavailable/stale approved policy indexes and
bad token signatures/audiences fail closed. FinCrime Agent now exports diagnostics
and fails startup rather than silently using a stub model or dropping security.
The frontend upstream/DNS resolver are runtime settings; use the platform resolver
instead of the Docker default when moving the same frontend image elsewhere.

## Deployment order

Automation validates real Compose, builds Linux images, generates identity keys
and matching client secrets, starts MariaDB/Keycloak and Qdrant, independently
ingests approved policies, starts application containers, validates DNS/Caddy,
then runs live smoke tests. See [policy runtime](live-policy-runtime.md) for the
same ingestion contract. Startup never silently creates an unapproved index.

## Scaling limitations

One replica owns each SQLite volume. Independent replicas cannot share database
files across hosts; SQLite serializes writers. Agent conversations, locks, task
sets and PII token bindings remain partly process-local. Checkpoints do not restore
every live session after restart. Single-node Qdrant, MariaDB and Keycloak/local
cache are not HA. Kubernetes requires an intentional persistence/session design,
platform probes/DNS and identity HA configuration. No shared-file service
communication or Azure dependency was added to banking or orchestration logic.

## Validation

Current results and remaining live Azure gates are recorded in
[VALIDATION.md](../infra/azure/VALIDATION.md). Actual Linux Compose builds,
Keycloak/MariaDB persistence, Nginx substitution, OIDC service tokens/JWKS and real
Qdrant/FastEmbed checks are automated by `infra/azure/validate.ps1`. Public DNS/TLS,
Azure provisioning, VM managed identity and Anthropic end-to-end deployment are
not claimed until an actual Azure run produces a passing smoke report.
