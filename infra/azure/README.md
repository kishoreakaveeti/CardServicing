# Azure deployment operations

The single source of truth is [AZURE_DEPLOYMENT.md](../../AZURE_DEPLOYMENT.md).
It covers prerequisites, the scripts/azure-*.ps1 entrypoints, free Azure DNS,
secure provisioning, HTTPS/OIDC, backups, troubleshooting, costs and teardown.

The existing advanced infra/azure commands remain supported with -ConfigPath;
the root guide documents every entrypoint and its parameters. Existing two-domain
configurations remain supported. Use their saved configuration rather than adopting
a new deployment ID. Local compose.yaml is unchanged.

Historical test evidence is in [VALIDATION.md](VALIDATION.md). Read the root
guide's validation section for checks executed and remaining live deployment gates.
