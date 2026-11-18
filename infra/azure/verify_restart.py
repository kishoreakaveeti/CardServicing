"""Restart only service-owned database/BFF processes, then verify saved synthetic state."""
import json
from pathlib import Path
import subprocess

ROOT=Path('/opt/cardservicing')
STATE=ROOT/'state'
base=['docker','compose','--env-file',str(STATE/'.env.azure'),'-f',str(ROOT/'app/compose.azure.yaml')]
try:
    if not (STATE/'smoke-customer.json').exists():
        results=[dict(check='restart-persistence',status='NOT TESTED',evidence='Synthetic banking smoke prerequisite failed')]
        (STATE/'restart-report.json').write_text(json.dumps(results))
    else:
        subprocess.run(base+['restart','--timeout','60','cards-api','fincrime-api','experience'],check=True)
        subprocess.run(base+['up','-d','--wait','--wait-timeout','360'],check=True)
        subprocess.run(base+['run','--rm','--no-deps','--user','0','--entrypoint','python',
            '-v',str(ROOT)+':/deployment','fincrime-api','/deployment/app/infra/azure/smoke.py','--persistence'],check=True)
except Exception as exc:
    (STATE/'restart-report.json').write_text(json.dumps([dict(check='restart-persistence',status='FAIL',evidence=type(exc).__name__)]))
