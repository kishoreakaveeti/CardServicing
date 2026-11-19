"""Run the PowerShell launcher against a recording Docker stub; no containers."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def launcher(tmp_path):
    powershell = shutil.which('powershell')
    if not powershell or os.name != 'nt':
        pytest.skip('Windows PowerShell launcher test')
    root = tmp_path / 'repository'
    (root / 'scripts').mkdir(parents=True)
    shutil.copyfile(Path('scripts/stack.ps1'), root / 'scripts/stack.ps1')
    (root / '.env.stack').write_text('')
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    log = tmp_path / 'docker.log'
    (bin_dir / 'docker.cmd').write_text(
        '@echo off\necho %*>>"%STACK_TEST_LOG%"\n'
        'if "%STACK_TEST_FAIL_INGEST%"=="1" (\n'
        ' echo %* | findstr /c:"pipeline.cli ingest" >nul\n'
        ' if not errorlevel 1 exit /b 9\n)\nexit /b 0\n')

    def run(*arguments, fail=False):
        env = {**os.environ, 'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],
               'STACK_TEST_LOG':str(log), 'STACK_TEST_FAIL_INGEST':'1' if fail else '0'}
        script = str(root / 'scripts/stack.ps1').replace("'", "''")
        result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command',
            "function Invoke-WebRequest { [pscustomobject]@{StatusCode=200} }; "
            f"& '{script}' " + ' '.join(arguments)], env=env, capture_output=True, text=True, timeout=30)
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls
    return run


def test_build_indexes_before_application_start(launcher):
    result,calls = launcher('-Build')
    assert result.returncode == 0, result.stderr
    assert len(calls) == 4, calls
    assert calls[0].endswith(' build')
    assert calls[1].endswith(' up -d --wait --wait-timeout 120 qdrant')
    assert '--entrypoint python cards-agent -m card_chat.policy.pipeline.cli ingest' in calls[2]
    assert calls[3].endswith(' up -d --wait --wait-timeout 360')
    assert 'Policy indexing completed' in result.stdout


def test_index_failure_aborts_startup(launcher):
    result,calls = launcher('-Build', fail=True)
    assert result.returncode != 0
    assert len(calls) == 3, calls
    assert 'Stack ready' not in result.stdout
    assert 'Policy indexing completed' not in result.stdout


def test_normal_start_refreshes_index_without_rebuilding(launcher):
    result,calls = launcher()
    assert result.returncode == 0, result.stderr
    assert len(calls) == 3
    assert calls[0].endswith(' qdrant')
    assert calls[1].endswith(' card_chat.policy.pipeline.cli ingest')


def test_stop_does_not_build_or_index(launcher):
    result,calls = launcher('-Stop')
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1 and calls[0].endswith(' down')


def test_debugger_attach_does_not_reindex(launcher):
    result,calls = launcher('-DebugComponent Cards-Agent', '-WaitForDebugger')
    assert result.returncode == 0, result.stderr
    assert not any('pipeline.cli' in call for call in calls)
    assert '--no-deps cards-agent' in calls[0]
