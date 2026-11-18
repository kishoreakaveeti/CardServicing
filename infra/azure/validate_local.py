"""Real Linux Docker preflight. Never imports or calls Azure tooling.

Each run owns a unique project/volumes and only removes its synthetic artifacts.
Existing local containers, credentials and persistent data are never selected.
"""
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def main():
    token = uuid4().hex[:12]
    project = 'cardservicing-check-' + token
    directory = ROOT / 'data' / project
    directory.mkdir(parents=True)
    os.chmod(directory, 0o700)
    report = []
    containers = []
    def docker(*args, code=None, capture=False):
        args = list(args)
        if code is not None:
            args.insert(args.index('run') + 1, '-T' if args[0] == 'compose' else '-i')
        result = subprocess.run(['docker', *map(str, args)], cwd=ROOT, input=code,
            text=True, stdout=subprocess.PIPE if capture else None, check=True)
        return result.stdout.strip() if capture else None
    def passed(name, evidence):
        report.append(dict(check=name, status='PASS', evidence=evidence))
        print('PASS:', name, flush=True)
    def wait(container):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = json.loads(docker('inspect', container, capture=True))[0]['State']
            if state.get('Health', {}).get('Status') == 'healthy':
                return
            if not state['Running']:
                docker('logs','--tail','30',container)
                raise RuntimeError('Validation container exited: ' + container)
            time.sleep(2)
        raise RuntimeError('Validation readiness timed out: ' + container)
    env_path = directory / 'validation.env'
    override = directory / 'ports.yaml'
    settings = dict(COMPOSE_PROJECT_NAME=project, TELEMETRY_SINK='stdout', STACK_MODEL_PROVIDER='stub',
        PUBLIC_ORIGIN='https://bank.validation.invalid', IDENTITY_PUBLIC_URL='https://identity.validation.invalid',
        OIDC_ISSUER='https://identity.validation.invalid/realms/cards',
        OIDC_AUTHORIZE_URL='https://identity.validation.invalid/realms/cards/protocol/openid-connect/auth',
        AZURE_STORAGE_ACCOUNT_URL='https://validation.blob.core.windows.net',
        IDENTITY_CONFIG_DIR='./data/' + project + '/identity')
    for volume in ('AGENT','EXPERIENCE','CARDS','FINCRIME','IDENTITY','QDRANT','IDENTITY_DB'):
        settings[volume + '_VOLUME'] = project + '-' + volume.lower().replace('_','-')
    env_path.write_text(''.join(f'{k}={v}\n' for k,v in settings.items()))
    identity_env = directory / 'identity.env'
    # Placeholders are sufficient to validate/build before generation.
    identity_env.write_text(''.join(f'{key}=validation-placeholder\n' for key in
        ('EXPERIENCE_SECRET','AGENT_SECRET','MCP_SECRET','BROWSER_SECRET','IDENTITY_ADMIN_PASSWORD','KEYCLOAK_DB_PASSWORD','KEYCLOAK_DB_ROOT_PASSWORD')))
    override.write_text('services:\n  identity:\n    ports: !reset []\n  frontend:\n    ports: !reset []\n')
    compose = ['compose','--env-file',str(env_path),'--env-file',str(identity_env),
               '-f','compose.azure.yaml','-f',str(override)]
    try:
        assert docker('info','--format','{{.OSType}}',capture=True) == 'linux', 'Select a Linux Docker engine'
        docker(*compose, 'config','--quiet')
        passed('compose-cli', 'Real Docker Compose configuration validation')
        docker(*compose, 'build')
        passed('linux-builds', 'All seven independent application images built')
        mount = 'type=bind,source=' + str(ROOT) + ',target=/work'
        docker('run','--rm','--user','0','--mount',mount,
            '-e','IDENTITY_OUTPUT_DIR=/work/data/' + project + '/identity',
            '-e','STACK_ENV_FILE=/work/data/' + project + '/identity.env',
            '-e','IGNORE_LOCAL_ENV=1','-e','PUBLIC_ORIGIN=https://bank.validation.invalid',
            '-e','KEYCLOAK_SSL_REQUIRED=external','--entrypoint','python',project + '-fincrime-api',
            '/work/scripts/setup_stack.py')
        # Make exactly the intended Linux readers work also on native Linux hosts.
        docker('run','--rm','--user','0','--mount',mount,'--entrypoint','python',project + '-fincrime-api','-',code=f'''
import os
from pathlib import Path
p=Path('/work/data/{project}/identity')
os.chown(p/'delegation-private.pem',0,10001)
os.chmod(p/'delegation-private.pem',0o640)
os.chown(p/'realm.json',0,0)
os.chmod(p/'realm.json',0o640)
''')
        for service in ('identity-db','identity','qdrant','frontend'):
            name = project + '-' + service
            docker(*compose,'run','-d','--no-deps','--use-aliases','--name',name,service)
            containers.append(name)
            wait(name)
        passed('keycloak-start', '26.4.7 production start with private persistent MariaDB 11.4 and local cache')
        docker(*compose,'run','--rm','--no-deps','frontend','sh','-c',
               '/docker-entrypoint.d/20-envsubst-on-templates.sh && nginx -t')
        passed('nginx-template', 'Actual entrypoint envsubst and nginx -t passed')
        docker('run','--rm','--mount','type=bind,source='+str(ROOT/'infra/azure/Caddyfile')+',target=/etc/caddy/Caddyfile,readonly',
               '-e','APP_DOMAIN=bank.validation.invalid','-e','IDENTITY_DOMAIN=identity.validation.invalid',
               '-e','ACME_EMAIL=validation@example.invalid','--entrypoint','caddy','caddy:2.10.2-alpine',
               'validate','--config','/etc/caddy/Caddyfile')
        passed('caddy-config', 'Pinned Caddy validates both HTTPS sites with HTTP challenge disabled')
        for script in ('bootstrap.sh','control.sh'):
            docker('run','--rm','--mount',mount,'--entrypoint','bash',project+'-fincrime-api',
                   '-n','/work/infra/azure/'+script)
        passed('linux-shell-syntax', 'Bootstrap and lifecycle scripts pass actual bash -n')
        docker(*compose,'run','--rm','--no-deps','--workdir','/data','--entrypoint','python',
               'cards-agent','-m','card_chat.policy.pipeline.cli','ingest')
        docker(*compose,'run','--rm','--no-deps','--entrypoint','python','cards-agent','-',code='''
import os
from card_servicing.startup import wait_for_dependencies
from card_chat.policy.factory import build_runtime_policy_services
wait_for_dependencies(['http://identity:9000/health/ready','http://qdrant:6333/readyz'])
r,_=build_runtime_policy_services(dict(os.environ))
assert r.retrieve('unblock_card',{'is_temporary_freeze': True})
print('PASS: actual FastEmbed ingestion, Qdrant readiness and policy retrieval')
''')
        passed('rag', 'Real Qdrant/FastEmbed ingestion, strict index checks and retrieval')
        docker(*compose,'run','--rm','--no-deps','--entrypoint','python','experience','-',code='''
import asyncio, os
from pathlib import Path
import httpx
from card_chat.security import Security
Path(os.environ['DELEGATION_PRIVATE_KEY']).read_bytes()
async def check():
 s=Security(dict(os.environ))
 token=await s.service_token()
 assert s.verify(token,'agent','agent:invoke','experience-service')
 async with httpx.AsyncClient() as client:
  r=await client.get('http://identity:8080/realms/cards/.well-known/openid-configuration',headers={'X-Forwarded-Proto':'https','X-Forwarded-Host':'identity.validation.invalid'})
  assert r.json()['issuer']==os.environ['OIDC_ISSUER']
  assert (await client.get('http://frontend:8080/health')).status_code==200
asyncio.run(check())
print('PASS: readable private key, real service token, issuer, JWKS and Nginx health')
''')
        passed('oidc-and-mounts', 'Real token/audience/issuer/JWKS verification and BFF signing-key readability')
        # An extra user created after import proves the MariaDB store survived restart.
        network = project + '_default'
        marker = 'persist-' + token
        verify_code = '''
import os, httpx
c=httpx.Client(base_url='http://identity:8080',headers={'X-Forwarded-Proto':'https','X-Forwarded-Host':'identity.validation.invalid'})
r=c.post('/realms/master/protocol/openid-connect/token',data={'grant_type':'password','client_id':'admin-cli','username':'admin','password':os.environ['IDENTITY_ADMIN_PASSWORD']})
r.raise_for_status()
c.headers['Authorization']='Bearer '+r.json()['access_token']
'''
        docker('run','--rm','-i','--network',network,'--env-file',identity_env,
               '--entrypoint','python',project+'-fincrime-api','-',code=verify_code +
               f"assert c.post('/admin/realms/cards/users',json={{'username':'{marker}','enabled':True}}).status_code==201\n")
        docker('restart',project+'-identity')
        wait(project+'-identity')
        docker('run','--rm','-i','--network',network,'--env-file',identity_env,
               '--entrypoint','python',project+'-fincrime-api','-',code=verify_code +
               f"assert len(c.get('/admin/realms/cards/users',params={{'username':'{marker}','exact':'true'}}).json())==1\n")
        passed('keycloak-persistence', 'Post-import synthetic user survived container restart')
        report.append(dict(check='live-cloud',status='NOT TESTED',evidence='No Azure calls, managed identity, public TLS or Anthropic credentials used'))
    except Exception as exc:
        report.append(dict(check='preflight',status='FAIL',evidence=type(exc).__name__))
        raise
    finally:
        for name in containers:
            subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL)
        # Delete only this run's uniquely named synthetic validation volumes/network.
        subprocess.run(['docker',*compose,'down','--volumes','--remove-orphans'],cwd=ROOT)
        (directory/'report.json').write_text(json.dumps(report,indent=2))
        print('Validation report:',directory/'report.json',flush=True)


if __name__ == '__main__':
    main()
