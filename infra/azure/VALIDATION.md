# Validation record - 18 November 2026

No Azure resources were provisioned. No Azure login, storage account key or
Anthropic key was used for the completed checks below. The normal local Docker
stack was inspected after validation: all ten existing local containers remained
running and healthy, including optional MongoDB. Isolated tests used separate
project/volume names and deleted only their own synthetic validation resources.

| Status | Check | Evidence |
| --- | --- | --- |
| PASS | Real Docker Compose CLI | Azure application configuration and separate proxy configuration validated with the installed Compose v5.7.0 CLI. |
| PASS | Linux application builds | All seven application image targets built on Docker Desktop's Linux/amd64 Engine 29.9.3. Dependency installation and embedding warmup completed. |
| PASS | Supported Keycloak database | Keycloak 26.4.7 ran in production profile with private MariaDB 11.4 and local cache. A synthetic post-import user remained after Keycloak container restart. |
| PASS | Nginx | Actual unprivileged-image environment substitution and `nginx -t` passed; frontend health returned HTTP 200. |
| PASS | Caddy | Pinned 2.10.2 image validated the two HTTPS sites and disabled HTTP challenge configuration. No public certificate was requested. |
| PASS | Linux shell scripts | Actual `bash -n` accepted bootstrap and lifecycle control scripts, including the final verification error handler. |
| PASS | OIDC and mounted keys | Real Keycloak client-credentials grant, expected audience/scope/issuer verification, private JWKS URL and BFF signing-key read succeeded on the isolated container network. |
| PASS | Real RAG | Four approved policies ingested using FastEmbed into Qdrant 1.13.4, strict index readiness and actual policy retrieval passed. Qdrant client 1.14.3 is now constrained to the compatible range. |
| PASS | Full Python regression suite | **310 passed, 1 skipped**, no failed tests. The skip was the opt-in real Qdrant test, subsequently run explicitly below. |
| PASS | Explicit live-Qdrant workflow plus focused checks | **15 passed**, including the previously skipped real Qdrant/FastEmbed HTTP customer-request test, deployment safety tests and Compose checks. The integration test created/deleted only its unique synthetic collection. |
| PASS | Final PowerShell safety checks | **3 passed** after final NSG/reporting changes: entry-point parsing, foreign-group/unexpected-resource refusal and safe configuration/unrestricted-SSH rejection. |
| PASS | Existing security and telemetry coverage | Regression includes Mongo provider/outage, Blob mocked failure, SQLite idempotency, MCP authorization, delegation, PII, orchestration and RAG policy enforcement. Added FinCrime startup-failure tests prove no silent model/security fallback. |
| PASS | Git whitespace validation | `git diff --check` clean; shell files are LF with a matching Git attributes rule. |
| FAIL | Final completed validation | No outstanding failures. Earlier discovered setup, realm-file permissions, compatibility and generated-guide defects were fixed and their checks rerun. |
| NOT TESTED | Live Azure provisioning | Azure CLI/subscription, regional SKU quota/capacity, resource-group/VM/network/storage creation, apt installation on the actual Ubuntu VM and scoped RBAC remain live deployment gates. |
| NOT TESTED | Managed identity and real Azure Blob | The deployment smoke script uploads and retrieves a unique marker using IMDS-backed managed identity and verifies per-service events. It has not been executed on Azure. |
| NOT TESTED | Public DNS / ACME / HTTPS browser workflow | Requires the operator's owned DNS names and reachable Azure VM. Local service tokens passed; this does not prove public certificate issuance, browser redirects or public login. |
| NOT TESTED | Anthropic end-to-end | Smoke harness exercises live natural-language routing and remote FinCrime calls once deployed with an authorized model/key. No paid model calls were made during preparation. |
| NOT TESTED | VM lifecycle and encrypted backup roundtrip | PowerShell safety/parsing and restore-manifest checks passed. Actual VM deallocation/restart and offline encrypted data backup/restore roundtrip have not been executed. Run a backup/restore rehearsal with synthetic data before relying on recovery. |
| NOT TESTED | External isolation against the Azure VM | Live smoke checks Docker publications, operator-side TCP probes and exact NSG SSH/HTTPS rules. These require the deployed address. |
| NOT TESTED | Keycloak self-registration | Intentionally disabled in the existing realm. Existing banking customer registration and Keycloak OIDC sign-in are separate paths; automation does not merge them or enable realm self-registration. |

The isolated Docker report is generated under
`data/cardservicing-check-<run-id>/report.json` by `validate.ps1`. The successful
final run used `cardservicing-check-c53161e9110e`. It recorded nine PASS checks and
one NOT TESTED cloud category. Detailed local logs are ignored under
`data/azure-validation/`. Live reports will be downloaded to
`.azure/<DeploymentId>/smoke-report.json` with PASS/FAIL/NOT TESTED and redacted
evidence. Deployment must not be described as successful until that report is
produced against the actual Azure environment.

## Changed files

New PowerShell entry points under `infra/azure/`: `deploy.ps1`, `start.ps1`,
`stop.ps1`, `status.ps1`, `destroy.ps1`, `backup.ps1`, `restore.ps1`,
`smoke-test.ps1`, and local-only `validate.ps1`. `common.ps1` provides validation,
ownership, native-command, secure transfer and host-key helpers.

Other new deployment files: `deployment.example.json`, `bootstrap.sh`,
`control.sh`, `configure.py`, `Caddyfile`, `compose.proxy.yaml`, `check_dns.py`,
`backup_data.py`, `smoke.py`, `verify_internal.py`, `verify_restart.py`,
`verify_blob.py`, `verify_isolation.py`, `report.py`, `validate_local.py`,
`README.md`, and this record.

Updated repository files: `compose.azure.yaml`, `.env.azure.example`,
`docs/azure-compose.md`, `.gitignore`, `pyproject.toml`, `scripts/setup_stack.py`,
`src/card_chat/fincrime_agent.py`, `src/card_chat/observability.py`,
`tests/test_cloud_compose.py`, and generated breakpoint links in
`docs/debugging-account-and-card.md`. Added `.gitattributes` and
`tests/test_azure_automation.py`.
