"""Reproduce the VM's umask/extraction failure in a cached Linux application image."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_source_is_importable_by_non_root_after_private_extraction():
    docker = shutil.which('docker')
    if not docker:
        candidate = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local'))) / 'Programs/DockerDesktop/resources/bin/docker.exe'
        if candidate.is_file():
            docker = str(candidate)
    if not docker:
        pytest.skip('Local Docker CLI unavailable')
    result = subprocess.run([docker, 'image', 'ls', '--format', '{{.Repository}}:{{.Tag}}'],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        pytest.skip('Local Linux Docker engine unavailable')
    images = [line for line in result.stdout.splitlines()
              if line.startswith('cardservicing-check-') and line.endswith('-cards-agent:latest')]
    if not images:
        pytest.skip('No cached Cards Agent preflight image; no images are pulled by this test')
    bootstrap = (ROOT/'infra/azure/bootstrap.sh').read_text()
    extractor = bootstrap.split('python3 - "$upload/release.tar.gz" "$stage" <<\'PY\'\n', 1)[1].split('\nPY\n', 1)[0]
    code = f'EXTRACTOR = {extractor!r}\n' + r'''
import os, stat, subprocess, sys, tarfile, tempfile
from pathlib import Path

assert Path('/app/src/card_chat/policy/pipeline/cli.py').is_file()
with tempfile.TemporaryDirectory(prefix='cardservicing-permissions-') as directory:
    root=Path(directory)
    root.chmod(0o755)
    archive=root/'source.tar'
    with tarfile.open(archive,'w') as output:
        output.add('/app/src',arcname='src')
    state=root/'state'
    state.mkdir(mode=0o700)
    secret=state/'fake-secret'
    secret.write_text('test-only')
    secret.chmod(0o600)
    old=root/'before'
    old.mkdir(mode=0o755)
    os.umask(0o077)
    with tarfile.open(archive) as source:
        source.extractall(old,filter='data')
    def probe(stage):
        return subprocess.run([sys.executable,'-c',
            'import card_chat.policy.pipeline.cli; import card_chat.policy; print(card_chat.policy.__file__)'],
            cwd='/data',env={**os.environ,'PYTHONPATH':str(stage/'src')},
            user=10001,group=10001,extra_groups=[],capture_output=True,text=True)
    before=probe(old)
    assert before.returncode and 'ModuleNotFoundError' in before.stderr and 'card_chat' in before.stderr, before.stderr
    print('REPRODUCED: private extraction prevents non-root policy imports',flush=True)
    fixed=root/'after'
    fixed.mkdir(mode=0o700)
    sys.argv=['bootstrap-extract',str(archive),str(fixed)]
    exec(compile(EXTRACTOR,'bootstrap.sh extraction','exec'),{})
    after=probe(fixed)
    assert after.returncode==0, after.stderr
    assert str(fixed/'src') in after.stdout, after.stdout
    assert stat.S_IMODE(state.stat().st_mode)==0o700
    assert stat.S_IMODE(secret.stat().st_mode)==0o600
    assert stat.S_IMODE(fixed.stat().st_mode)==0o755
    print('PASS: exact bootstrap extraction restores non-root policy imports and preserves private state',flush=True)
'''
    run = subprocess.run([docker, 'run', '--rm', '-i', '--network', 'none', '--user', '0',
                          '--entrypoint', 'python', images[0], '-'],
                         input=code, text=True, capture_output=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert 'REPRODUCED:' in run.stdout and 'PASS:' in run.stdout
