"""Inspect actual Docker publications; only Caddy's host network exposes HTTPS."""
import json
from pathlib import Path
import subprocess

def main(state=Path('/opt/cardservicing/state')):
    try:
        base=['docker','compose','--env-file',str(state/'.env.azure'),'-f','compose.azure.yaml']
        cfg=json.loads(subprocess.check_output(base+['config','--format','json'],text=True))
        for name,service in cfg['services'].items():
            if name not in ('identity','identity-db','qdrant','frontend','opa'):
                assert service['environment']['TELEMETRY_SINK']=='azure_blob'
            assert name not in ('mongodb','redis','rabbitmq')
        # COMPOSE_PROJECT_NAME can place the Caddy proxy in this same project.
        # Inspect the services in the application configuration explicitly.
        ids=subprocess.check_output(base+['ps','-q',*cfg['services']],text=True).split()
        assert len(ids)==len(cfg['services'])
        containers=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
        for container in containers:
            for binds in container['NetworkSettings']['Ports'].values():
                assert not binds or all(b['HostIp']=='127.0.0.1' for b in binds)
        results=[dict(check='internal-isolation',status='PASS',evidence=f'All {len(cfg["services"])} live application/identity containers inspected; only loopback publications, Blob selected and no Mongo/Redis/RabbitMQ')]
    except Exception as exc:
        results=[dict(check='internal-isolation',status='FAIL',evidence=type(exc).__name__)]
    (state/'isolation-report.json').write_text(json.dumps(results))


if __name__=='__main__':
    main()
