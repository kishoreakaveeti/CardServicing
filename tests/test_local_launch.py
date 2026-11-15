import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_services', ROOT / 'scripts/local_service.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


@pytest.mark.parametrize('component', ['experience', 'agent', 'mcp', 'cards'])
def test_native_settings_use_local_destinations_and_separate_storage(monkeypatch, component):
    config = {'EXPERIENCE_SECRET': 'test-experience', 'AGENT_SECRET': 'test-agent',
              'MCP_SECRET': 'test-mcp', 'BROWSER_SECRET': 'test-browser',
              'ANTHROPIC_API_KEY': 'test-model-key', 'STACK_MODEL_PROVIDER': 'stub'}
    monkeypatch.setattr(native, 'dotenv_values', lambda path: config)
    # Restore process environment after this test's launcher configuration.
    monkeypatch.setattr(native.os, 'environ', dict(config))
    native.configure(component)
    env = native.os.environ
    assert env['AGENT_URL'] == 'http://127.0.0.1:8021'
    assert env['MCP_URL'] == 'http://127.0.0.1:8030/mcp'
    assert env['CARD_API_BASE_URL'] == 'http://127.0.0.1:8010'
    assert Path(env['SQLITE_PATH']) == ROOT / 'data/local/cards.db'
    assert 'identity:8080' not in env['OIDC_TOKEN_URL']
    assert ('ANTHROPIC_API_KEY' in env) == (component == 'agent')
    if component != 'experience':
        assert 'DELEGATION_PRIVATE_KEY' not in env


def test_browser_readiness_signals_only_when_every_service_is_ready(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location('wait_local', ROOT / 'scripts/wait_local.py')
    readiness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(readiness)
    monkeypatch.setattr(readiness, 'responding', lambda url: True)
    readiness.wait_for_routes(timeout=0)
    assert 'LOCAL_HTTP_READY:' in capsys.readouterr().out
    monkeypatch.setattr(readiness, 'responding', lambda url: ':8080/' not in url)
    with pytest.raises(RuntimeError, match='Frontend: http://127.0.0.1:8080/'):
        readiness.wait_for_routes(timeout=0)
    assert 'LOCAL_HTTP_READY:' not in capsys.readouterr().out


def test_local_browser_wait_has_a_background_readiness_matcher():
    import json
    tasks = json.loads((ROOT / '.vscode/tasks.json').read_text())['tasks']
    wait = next(t for t in tasks if t['label'] == 'Wait for Local HTTP')
    assert wait['isBackground']
    assert wait['problemMatcher']['background']['endsPattern'] == '^LOCAL_HTTP_READY:'
    launch = json.loads((ROOT / '.vscode/launch.json').read_text())
    local = next(c for c in launch['compounds'] if c['name'] == 'Debug Full Stack (Local)')
    assert local['preLaunchTask'] == 'Prepare Local Stack'
    assert len(local['configurations']) == 5
