from pathlib import Path
import yaml


def test_azure_compose_service_boundaries_and_private_storage():
    document = yaml.safe_load(Path('compose.azure.yaml').read_text())
    services = document['services']
    assert set(services) == {'frontend', 'experience', 'cards-agent', 'fincrime-agent', 'mcp-server',
                             'cards-api', 'fincrime-api', 'identity', 'identity-db', 'qdrant'}
    for name, service in services.items():
        assert 'container_name' not in service
        assert 'healthcheck' in service
        assert service['restart'] == 'unless-stopped'
        for port in service.get('ports', []):
            assert port.startswith('127.0.0.1:')
        assert all(dep in services for dep in service.get('depends_on', {}))
    volumes = []
    for name in ('experience', 'cards-agent', 'cards-api', 'fincrime-api'):
        service = services[name]
        assert service['environment']['TELEMETRY_SINK'] == '${TELEMETRY_SINK:-azure_blob}'
        volumes.extend(v for v in service['volumes'] if v.endswith(':/data'))
    assert len(volumes) == len(set(volumes)) == 4
    assert services['experience']['environment']['COOKIE_SECURE'] == 'true'
    assert services['identity']['command'][0] == 'start'


def test_local_compose_keeps_mongo():
    services = yaml.safe_load(Path('compose.yaml').read_text())['services']
    assert 'mongodb' in services
    assert services['cards-api']['environment']['TELEMETRY_SINK'] == '${TELEMETRY_SINK:-mongo}'


def test_keycloak_uses_private_supported_database():
    services = yaml.safe_load(Path('compose.azure.yaml').read_text())['services']
    assert services['identity']['environment']['KC_DB'] == 'mariadb'
    assert 'ports' not in services['identity-db']
    assert services['identity-db']['volumes'] == ['identity_db_data:/var/lib/mysql']
    assert services['identity']['depends_on']['identity-db']['condition'] == 'service_healthy'
