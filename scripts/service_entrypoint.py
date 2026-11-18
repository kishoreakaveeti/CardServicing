"""One worker, no reload; debug listeners never block HTTP readiness by default."""
import os
import runpy
import sys

from card_servicing.startup import wait_for_dependencies

wait_for_dependencies(os.environ.get('STARTUP_CHECK_URLS', '').split(),
                      timeout=float(os.environ.get('STARTUP_TIMEOUT_SECONDS', '60')))

if os.environ.get('DEBUG_ENABLED') == '1':
    import debugpy
    debugpy.listen(('0.0.0.0', 5678))
    if os.environ.get('DEBUG_WAIT') == '1':
        debugpy.wait_for_client()
sys.argv = ['uvicorn', os.environ['APP_MODULE'], '--host', '0.0.0.0', '--port', '8000',
            '--no-access-log']
runpy.run_module('uvicorn', run_name='__main__')
