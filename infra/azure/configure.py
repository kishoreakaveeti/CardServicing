"""Initialize deployment state in a tools container; never read local demo secrets."""
import json
import os
from pathlib import Path
import subprocess
from dotenv import dotenv_values

ROOT = Path('/deployment')
state = ROOT / 'state'
cfg = json.loads((state / 'bootstrap.json').read_text())
public = 'https://' + cfg['AppDomain']
identity = 'https://' + cfg['IdentityDomain']
existing = state / 'deployment.json'
if existing.exists():
    previous = json.loads(existing.read_text())
    for field in ('DeploymentId', 'AppDomain', 'IdentityDomain', 'StorageAccount', 'StorageContainer'):
        if previous[field] != cfg[field]:
            raise SystemExit('Existing identity/storage settings differ; reconcile them before redeploying.')
environment = {**os.environ, 'IDENTITY_OUTPUT_DIR': str(state / 'identity'),
    'STACK_ENV_FILE': str(state / '.env.stack'), 'IGNORE_LOCAL_ENV': '1',
    'PUBLIC_ORIGIN': public, 'KEYCLOAK_SSL_REQUIRED': 'external'}
subprocess.run(['python', str(ROOT / 'app/scripts/setup_stack.py')], env=environment, check=True)
secrets = dotenv_values(state / '.env.stack')
values = {key: secrets[key] for key in ('EXPERIENCE_SECRET','AGENT_SECRET','MCP_SECRET',
    'BROWSER_SECRET','IDENTITY_ADMIN_PASSWORD','ACCOUNT_HASH_KEY','KEYCLOAK_DB_PASSWORD','KEYCLOAK_DB_ROOT_PASSWORD')}
values.update(COMPOSE_PROJECT_NAME='card-servicing-azure', TELEMETRY_SINK='azure_blob',
    AZURE_STORAGE_ACCOUNT_URL=f"https://{cfg['StorageAccount']}.blob.core.windows.net",
    AZURE_STORAGE_CONTAINER=cfg['StorageContainer'],
    PUBLIC_ORIGIN=public, IDENTITY_PUBLIC_URL=identity, OIDC_ISSUER=identity + '/realms/cards',
    OIDC_AUTHORIZE_URL=identity + '/realms/cards/protocol/openid-connect/auth',
    # These are daemon-host paths, not paths inside this initialization container.
    IDENTITY_CONFIG_DIR='/opt/cardservicing/state/identity',
    POLICIES_HOST_DIR='/opt/cardservicing/app/policies',
    STACK_MODEL_PROVIDER='anthropic', MODEL_ID=cfg['ModelId'],
    ANTHROPIC_API_KEY=cfg['AnthropicApiKey'], APP_DOMAIN=cfg['AppDomain'],
    IDENTITY_DOMAIN=cfg['IdentityDomain'], ACME_EMAIL=cfg['AcmeEmail'])
# Single quotes inhibit Compose interpolation, including any secret '$' characters.
if any("'" in str(value) or '\n' in str(value) or '\r' in str(value) for value in values.values()):
    raise SystemExit('Unsafe environment-file value')
(state / '.env.azure').write_text(''.join(f"{key}='{value}'\n" for key, value in values.items()))
(state / '.env.azure').chmod(0o600)
public_cfg = {key: value for key, value in cfg.items() if key != 'AnthropicApiKey'}
existing.write_text(json.dumps(public_cfg, indent=2))
existing.chmod(0o600)
print('Initialized deployment-owned credentials, callback, issuer and managed-identity telemetry.')

# Azure's free public IP hostname serves identity paths and the application.
if cfg['AppDomain'] == cfg['IdentityDomain']:
    (ROOT / 'app/infra/azure/Caddyfile').write_text(
        (ROOT / 'app/infra/azure/Caddyfile.single').read_text())
