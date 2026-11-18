# Deploy CardServicing to a single Azure VM

Nothing is provisioned by reading these files or running `validate.ps1`.
`deploy.ps1` provisions only when you explicitly execute it. It uses Azure CLI,
SSH and Docker Compose; there is no Terraform, Kubernetes or managed database.

## Prerequisites and configuration

Use PowerShell 5.1+ (7 recommended), Python 3.10+ (standard library only for
preflight), Azure CLI, OpenSSH `ssh`/`scp`, `tar`, and a running Linux Docker
engine. Azure CLI must be on PATH. On Windows the script also discovers Docker
Desktop in its usual install locations. Install tools before running; scripts do
not install tools or sign you in on your workstation.

The Azure operator needs permission to create the group's Compute, Network and
Storage resources, and to create container-scoped role assignments (for example
Contributor plus Role Based Access Control Administrator at the intended scope).
The script pins every Azure call to the configured subscription and refuses an
existing resource group with a different deployment ownership tag. It also
refuses unexpected resources in an owned group before redeployment/destruction.
Use a **new dedicated group**, never an existing shared application group.

From the repository root:

```powershell
New-Item -ItemType Directory -Force .azure | Out-Null
Copy-Item infra/azure/deployment.example.json .azure/deployment.json
ssh-keygen -t ed25519 -f "$HOME/.ssh/cardservicing-demo"
az login
az account list --output table
```

Edit `.azure/deployment.json`. Supply your actual subscription UUID, a stable
unique `DeploymentId`, resource group, globally unique lowercase Storage account
name, two owned public DNS hostnames, ACME contact email and your public SSH
source IP as `/32` (at most `/24` is accepted). Use a private key unlocked in your
SSH agent or without an interactive passphrase; batch SSH cannot prompt for it.
Keep its matching `.pub` file beside it. The script obtains the VM's SSH host key
through authenticated Azure Run Command, then enforces strict host-key checking.
It never disables verification or uses `ssh-keyscan` as a trust source.

Put the Anthropic API key in the file named by `AnthropicKeyFile`, restrict that
file to your user, and choose a model your account can call. No key is embedded
in JSON configuration or passed to Azure CLI/cloud-init. It is sent over verified
SSH into a root-only runtime configuration and never printed. Local transient
secret files are stored in a private ignored deployment directory and removed
after transfer. Never commit `.azure`, `.env.azure` or identity material.

`Standard_B4ms` (4 vCPU, 16 GiB) is the default demo baseline. The script checks
actual regional SKU capabilities and rejects sizes below 4 vCPU/16 GiB. Serial
builds reduce peak memory. Burstable CPU credits can make long builds slower;
choose an available 4-vCPU/16-GiB D-series size if needed. This is a sizing
baseline, not a load-test guarantee. Verify current subscription quota,
availability and Azure prices before deploying.

The 128-GiB Standard SSD **managed OS disk** holds `/var/lib/docker` and
`/opt/cardservicing`; it is persistent across container restarts, VM restarts and
deallocation. No state is placed on the temporary Azure resource disk. Separate
named volumes remain owned by each application service. This simple setup does
not survive deletion of the dedicated resource group without an external backup.

## Validate and deploy

```powershell
# No Azure calls: isolated Linux builds and runtime prerequisite checks.
.\infra\azure\validate.ps1

# Provisions only when you run this command. It repeats Linux preflight first.
.\infra\azure\deploy.ps1 -ConfigPath .azure/deployment.json
```

The script creates a dedicated group in **Australia East**, a VNet, Standard
static public IPv4, NSG, key-only Ubuntu 24.04 VM, system-assigned managed identity,
private Blob container and container-scoped Storage Blob Data Contributor grant.
The storage account disallows anonymous Blob access and shared-key authorization.
Container creation uses ARM, so the operator does not need a Blob data-plane role.
Application telemetry uses managed identity exclusively in this deployment.

When the VM IP is printed, create **A records for both configured hostnames**
pointing to that IP. Remove conflicting AAAA/old A records and disable CDN/proxy
fronting for this direct-VM demo. The VM verifies both names before Caddy starts;
DNS readiness has a ten-minute deadline. If it expires, fix DNS and rerun the same
deploy command. Existing credentials, identity database and volumes are preserved.
DNS setup is a necessary operator action; the script cannot manage an unspecified
DNS provider and will not invent a hostname.

Caddy uses public ACME **TLS-ALPN-01 on TCP 443**, with HTTP challenges and HTTP
redirect listeners disabled. Only TCP 443 and operator-restricted TCP 22 are
allowed inbound by the NSG. No public HTTP port, backend API, vector database or
identity database port is opened. Ensure port 443 is reachable for issuance and
renewal, and that domain CAA policy permits the selected ACME issuer. Invalid DNS
or an untrusted certificate makes smoke verification fail; there is no TLS bypass.

The installer uses Docker's signed Ubuntu apt repository, Engine and Compose
plugins, copies an explicit source-artifact allowlist over SSH, builds on Linux,
initializes deployment-specific keys/credentials, waits for identity/Qdrant,
ingests approved policies, starts all application containers and Caddy, then
runs smoke verification. It installs a systemd unit for ordered VM boot/shutdown.
Rerunning replaces application source and recreates containers; it preserves keys,
client secrets, accounts and data. It does not automatically rotate credentials,
change an existing realm's issuer/callbacks, upgrade databases or roll back failed
application upgrades. Use a backup before updates. Changed public identity/storage
settings are refused until explicitly reconciled.

The source and tooling are under `/opt/cardservicing/app`; root-only configuration
is under `/opt/cardservicing/state`. Only the BFF receives the delegation private
key (GID 10001). The pinned Keycloak image reads the realm with GID 0; the mounted
realm is root-owned mode 640, not world-readable. Public key mode is 644. Images
contain no credentials. Ordinary containers never mount the entire state tree;
only explicitly invoked privileged initialization/verification tooling does.

## Keycloak database correction

Keycloak 26.4.7 can start with embedded H2 but Keycloak explicitly does **not**
support `dev-file` in production mode. The Azure profile now uses a **private
MariaDB 11.4 container** solely for Keycloak with its own persistent volume and
separate generated root/application passwords. It adds no managed database and
migrates none of the banking SQLite data. Keycloak runs `start`, local cache,
public HTTPS hostname and trusted reverse-proxy headers. Local `compose.yaml`
retains its existing development identity setup and optional Mongo telemetry.

A pre-existing Azure H2 realm is **not automatically migrated**. This automation
initializes a fresh demo identity database. If you manually used the old Azure
profile with meaningful identity data, export/import that realm before changing
providers; do not remove the old identity volume blindly.

## Verify and inspect

```powershell
.\infra\azure\smoke-test.ps1 -ConfigPath .azure/deployment.json
.\infra\azure\status.ps1 -ConfigPath .azure/deployment.json
```

Reports use PASS, FAIL and NOT TESTED with non-sensitive evidence and are saved
at `.azure/<DeploymentId>/smoke-report.json`. A failed mandatory check or missing
report makes verification/deployment return an error. Tests use fresh synthetic
application customers and the generated synthetic Keycloak test identity. They
never print credentials, cookies, account numbers, card identifiers or responses.
Smoke tests create retained synthetic demo records and make billable Anthropic
calls. Do not use this harness against real customer data.

Verification covers trusted public TLS, exact OIDC issuer, browser Authorization
Code/PKCE login, secure session, CSRF and anonymous rejection; application customer
registration; reviewed account creation/card issuance, temporary blocking and
unblocking with idempotent confirmations; natural-language model routing; remote
FinCrime invocation; wrong MCP audiences and tampered delegation; actual strict
Qdrant/FastEmbed policy retrieval; persisted login and card state after restarting
Cards API, FinCrime API and BFF; real managed-identity Blob upload/correlation and
per-service telemetry; and inspected private Docker port publications.

Existing Keycloak realm self-registration is disabled intentionally. The app's
`/auth/register` creates a banking customer in the existing banking identity path;
it does not create a Keycloak user. The report marks Keycloak self-registration
NOT TESTED instead of enabling it or claiming both identity stores are merged.
Keycloak browser login is tested separately. Model output can vary: unexpected
routing fails the test and must be investigated, not hidden by a stub fallback.

## Stop, deallocate and restart

```powershell
# Stop containers gracefully and deallocate compute; preserve all data.
.\infra\azure\stop.ps1 -ConfigPath .azure/deployment.json

# Stop containers only; compute charges continue.
.\infra\azure\stop.ps1 -ConfigPath .azure/deployment.json -ServicesOnly

# Allocate a stopped VM, wait for SSH, and start services.
.\infra\azure\start.ps1 -ConfigPath .azure/deployment.json

# Safely stop/start containers while preserving volumes.
.\infra\azure\start.ps1 -ConfigPath .azure/deployment.json -RestartContainers
```

Deallocation stops VM compute charges. **Managed OS disk, static public IP and
Blob storage still incur charges.** A stopped-but-allocated VM also incurs compute
charges. No automatic spending limit is configured. Status lists the group's
resources and memory/disk usage without dumping secrets or container environments.

## Consistent encrypted backup and restore

Install `age` locally and generate an identity. Keep the private identity separate
from downloaded backups and the VM. Never commit it:

```powershell
New-Item -ItemType Directory -Force "$HOME/.secrets" | Out-Null
age-keygen -o "$HOME/.secrets/cardservicing-backup.agekey"
# Use the PUBLIC age1... recipient printed by age-keygen below.
.\infra\azure\backup.ps1 -ConfigPath .azure/deployment.json `
  -AgeRecipient 'YOUR_PUBLIC_AGE_RECIPIENT' -OutputDirectory .azure/backups

# Replaces current data: exact deployment-ID confirmation is mandatory.
.\infra\azure\restore.ps1 -ConfigPath .azure/deployment.json `
  -BackupFile '.azure/backups/YOUR_BACKUP.tar.gz.age' `
  -AgeIdentityFile "$HOME/.secrets/cardservicing-backup.agekey" `
  -ConfirmRestoreDeployment 'YOUR_CONFIGURED_DEPLOYMENT_ID'
```

The VM installs `age`. Backup pulls its helper image before stopping writers,
then stops Caddy and all application/identity/vector containers. With every writer
stopped, it archives complete SQLite/WAL files, Qdrant state, MariaDB/Keycloak state,
sessions/checkpoints, keys, runtime configuration and Caddy certificate volumes.
This is a coordinated offline snapshot with downtime, not a hot database file
copy. State is encrypted to the operator's age recipient before download. No
plaintext backup is transferred or uploaded to Blob telemetry storage. Temporary
plaintext staging is root-only and removed when the operation finishes.

Restore validates archive paths, deployment ID, source-release hash and exact
volume set before replacement. Deploy the same source release as the backup
first. It creates an encrypted pre-restore safety backup before replacing state.
The restore entry point starts services and runs smoke verification after replacement. Keep a copy of the source release with every backup.
Losing the age private identity makes the encrypted backups unrecoverable.
Downloaded backups survive resource-group destruction; VM-only backup copies do
not. Interrupted restore needs operator recovery from its safety backup; this is
not an HA disaster-recovery system. Backup/restore operations are serialized with
deployment/lifecycle operations using a host control lock.

## Destroy only this demo

```powershell
.\infra\azure\destroy.ps1 -ConfigPath .azure/deployment.json `
  -ConfirmDestroyDeployment 'YOUR_CONFIGURED_DEPLOYMENT_ID' -DeletePersistentData
```

Both confirmation arguments are required. The script checks group ownership and
refuses unexpected resources, then deletes only the dedicated group, including
its managed disk, public IP, VM, network and telemetry storage. All persistent
application/identity/Blob data in that group is deleted. Back up externally first.
Unrelated resource groups and local downloaded backups are not deleted.

## Limits and validation evidence

See [VALIDATION.md](VALIDATION.md) for completed checks and unverified cloud gates.
No Azure deployment is claimed until the live smoke report passes.

The application containers remain independent and cloud-agnostic. SQLite volumes
need one writer/replica; the agent's in-memory conversations, locks, pending tasks
and PII vault do not all survive process restart or support arbitrary horizontal
replicas. Journals/checkpoints do not restore all private bindings. Active
conversations may need restarting even though committed banking state persists.
Single Qdrant, MariaDB and Keycloak/local cache are not HA. Future Kubernetes
work requires durable single-writer storage or a deliberate persistence migration,
shared session/state coordination, platform DNS/probes and an identity HA plan.
No cross-service database files or file-based communication have been introduced.

References: [Keycloak database support](https://www.keycloak.org/server/db),
[Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/),
[Caddy HTTPS and challenges](https://caddyserver.com/docs/automatic-https),
[Azure CLI VM reference](https://learn.microsoft.com/en-us/cli/azure/vm), and
[Blob managed identity](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication-azure-hosted-apps).
