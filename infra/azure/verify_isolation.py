"""Inspect actual Docker publications; only Caddy's host network exposes HTTPS."""
import json
from pathlib import Path
import subprocess

state=Path('/opt/cardservicing/state')
try:
    base=['docker','compose','--env-file',str(state/'.env.azure'),'-f','compose.azure.yaml']
    cfg=json.loads(subprocess.check_output(base+['config','--format','json'],text=True))
    for name,service in cfg['services'].items():
        if name not in ('identity','identity-db','qdrant','frontend'):
            assert service['environment']['TELEMETRY_SINK']=='azure_blob'
        assert name not in ('mongodb','redis','rabbitmq')
    ids=subprocess.check_output(base+['ps','-q'],text=True).split()
    assert len(ids)==10
    containers=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
    for container in containers:
        for binds in container['NetworkSettings']['Ports'].values():
            assert not binds or all(b['HostIp']=='127.0.0.1' for b in binds)
    results=[dict(check='internal-isolation',status='PASS',evidence='All ten live application/identity containers inspected; only loopback publications, Blob selected and no Mongo/Redis/RabbitMQ')]
except Exception as exc:
    results=[dict(check='internal-isolation',status='FAIL',evidence=type(exc).__name__)]
(state/'isolation-report.json').write_text(json.dumps(results))
