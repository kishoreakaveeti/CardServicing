"""One worker, no reload; debug listeners never block HTTP readiness by default."""
import os
import runpy
import sys

if os.environ.get('DEBUG_ENABLED') == '1':
    import debugpy
    debugpy.listen(('0.0.0.0', 5678))
    if os.environ.get('DEBUG_WAIT') == '1':
        debugpy.wait_for_client()
sys.argv = ['uvicorn', os.environ['APP_MODULE'], '--host', '0.0.0.0', '--port', '8000',
            '--no-access-log']
runpy.run_module('uvicorn', run_name='__main__')
