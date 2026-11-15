"""Native, single-process service entry point for VS Code and PowerShell."""
import argparse
import os
from pathlib import Path
import time
import urllib.request

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {
    'experience': ('card_chat.experience:app', 8020, 'http://127.0.0.1:8021/health'),
    'agent': ('card_chat.main:app', 8021, 'http://127.0.0.1:8030/health'),
    'mcp': ('card_chat.mcp_server:app', 8030, 'http://127.0.0.1:8010/health/ready'),
    'cards': ('card_servicing.main:app', 8010, None),
}


def configure(component):
    config = {**dotenv_values(ROOT / '.env'), **dotenv_values(ROOT / '.env.stack'), **os.environ}
    issuer = config.get('LOCAL_OIDC_ISSUER', 'http://localhost:8081/realms/cards')
    endpoint = issuer + '/protocol/openid-connect/'
    values = {'OIDC_ISSUER': issuer, 'OIDC_JWKS_URL': endpoint + 'certs',
        'OIDC_TOKEN_URL': endpoint + 'token', 'OIDC_AUTHORIZE_URL': endpoint + 'auth',
        'DELEGATION_PUBLIC_KEY': str(ROOT / 'data/identity/delegation-public.pem'),
        'PUBLIC_ORIGIN': 'http://localhost:8080', 'AGENT_URL': 'http://127.0.0.1:8021',
        'MCP_URL': 'http://127.0.0.1:8030/mcp', 'MCP_RESOURCE': 'http://127.0.0.1:8030/mcp',
        'CARD_API_BASE_URL': 'http://127.0.0.1:8010', 'CARD_STORE': 'sqlite', 'TELEMETRY_SINK': 'none',
        'SQLITE_PATH': str(ROOT / 'data/local/cards.db'), 'AGENT_DB': str(ROOT / 'data/local/agent.db'),
        'SESSION_DB': str(ROOT / 'data/local/sessions.db')}
    identities = {'experience': ('experience-service', 'EXPERIENCE_SECRET'),
                  'agent': ('agent-service', 'AGENT_SECRET'), 'mcp': ('mcp-service', 'MCP_SECRET')}
    if component in identities:
        client, secret = identities[component]
        values.update(SERVICE_CLIENT_ID=client, SERVICE_CLIENT_SECRET=config[secret])
    if component == 'experience':
        values.update(BROWSER_CLIENT_SECRET=config['BROWSER_SECRET'],
                      DELEGATION_PRIVATE_KEY=str(ROOT / 'data/identity/delegation-private.pem'))
    if component == 'agent':
        values.update(MODEL_PROVIDER=config.get('STACK_MODEL_PROVIDER', 'stub'),
                      MODEL_ID=config.get('MODEL_ID', ''), ANTHROPIC_API_KEY=config.get('ANTHROPIC_API_KEY', ''),
                      ANTHROPIC_PROMPT_CACHE=config.get('ANTHROPIC_PROMPT_CACHE', 'false'))
    else:
        os.environ.pop('ANTHROPIC_API_KEY', None)
    os.environ.update({key: str(value) for key, value in values.items()})
    (ROOT / 'data/local').mkdir(parents=True, exist_ok=True)
    return config


def wait_for(url, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(.3)
    raise RuntimeError('Dependency did not become ready: ' + url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=SERVICES)
    args = parser.parse_args()
    configure(args.component)
    module, port, dependency = SERVICES[args.component]
    wait_for(os.environ['OIDC_ISSUER'] + '/.well-known/openid-configuration')
    if dependency:
        wait_for(dependency)
    import uvicorn
    uvicorn.run(module, host='127.0.0.1', port=port, access_log=False)


if __name__ == '__main__':
    main()
