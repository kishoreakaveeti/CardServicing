"""Fail early with actionable local-development prerequisites, without Docker."""
from pathlib import Path
import shutil
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from local_service import configure, wait_for

if not shutil.which('npm'):
    raise SystemExit('Install Node.js 22 LTS with npm, then restart VS Code so its PATH updates.')
if not (ROOT / 'frontend/node_modules/vite').exists():
    raise SystemExit('Run npm ci from the frontend directory first.')
for path in ('.env.stack', 'data/identity/delegation-public.pem', 'data/identity/delegation-private.pem'):
    if not (ROOT / path).exists():
        raise SystemExit('Run scripts/setup-stack.ps1 first. Missing: ' + path)
config = configure('cards')
import os
wait_for(os.environ['OIDC_ISSUER'] + '/.well-known/openid-configuration', timeout=5)
for port in (8080, 8010, 8020, 8021, 8030):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            raise SystemExit(f'Port {port} is occupied. Stop Docker app services and existing local sessions first.')
print('Local prerequisites ready. Identity is external; application processes will run on Windows.')
