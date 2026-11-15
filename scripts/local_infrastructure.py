"""Prepare only missing local-debug dependencies; application code stays native."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import urllib.request
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def identity_ready(url):
    try:
        # Local startup must not be routed through a Windows/corporate HTTP proxy.
        endpoint = url + '/.well-known/openid-configuration'
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
                  if urlsplit(url).hostname in ('localhost','127.0.0.1','::1') else None)
        with (opener.open(endpoint, timeout=2) if opener else urllib.request.urlopen(endpoint, timeout=2)) as response:
            return response.status == 200
    except OSError:
        return False


def mongo_ready(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def docker_command():
    command = shutil.which('docker')
    if command:
        return command
    for root, suffix in ((os.environ.get('LOCALAPPDATA'), 'Programs/DockerDesktop/resources/bin/docker.exe'),
                         (os.environ.get('ProgramFiles'), 'Docker/Docker/resources/bin/docker.exe')):
        if root:
            path = Path(root) / suffix
            try:
                if path.is_file():
                    return str(path)
            except OSError:
                pass
    raise RuntimeError('Start Identity and MongoDB manually, or start Docker Desktop. Docker CLI was not found.')


def prepare(config):
    issuer = config.get('LOCAL_OIDC_ISSUER', 'http://localhost:8081/realms/cards')
    missing = []
    if not identity_ready(issuer):
        if issuer not in ('http://localhost:8081/realms/cards', 'http://127.0.0.1:8081/realms/cards'):
            raise RuntimeError('Configured external Identity service is unavailable: ' + issuer)
        missing.append('identity')
    host = config.get('LOCAL_MONGODB_HOST', '127.0.0.1')
    if config.get('TELEMETRY_SINK', 'mongo') == 'mongo' and not mongo_ready(host, int(config.get('MONGODB_HOST_PORT', '27018'))):
        if host not in ('127.0.0.1', 'localhost'):
            raise RuntimeError('Configured external MongoDB service is unavailable.')
        missing.append('mongodb')
    if not missing:
        print('Local infrastructure is already available.', flush=True)
        return
    if not (ROOT / '.env.stack').exists():
        raise RuntimeError('Run scripts/setup-stack.ps1 once before local debugging.')
    print('Starting local-debug Docker dependencies: ' + ', '.join(missing) +
          '. The application services will run natively in VS Code.', flush=True)
    args = [docker_command(), 'compose']
    for env_file in ('.env', '.env.stack'):
        if (ROOT / env_file).exists():
            args += ['--env-file', str(ROOT / env_file)]
    args += ['-f', str(ROOT / 'compose.yaml'), 'up', '-d', '--wait', '--wait-timeout', '180', *missing]
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode:
        raise RuntimeError('Local dependency startup failed. Check Docker Desktop and the container errors above.')
    if not identity_ready(issuer):
        raise RuntimeError('Identity container started, but the cards realm is unavailable. Check its imported realm and logs.')
    print('Local infrastructure ready.', flush=True)


if __name__ == '__main__':
    try:
        prepare({**dotenv_values(ROOT / '.env'), **dotenv_values(ROOT / '.env.stack'), **os.environ})
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except KeyboardInterrupt:
        raise SystemExit('Local dependency startup cancelled.') from None
