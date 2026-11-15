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
