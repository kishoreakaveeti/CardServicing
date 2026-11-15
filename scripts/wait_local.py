"""Browser readiness runs alongside, not before, native Python debug launches."""
from local_service import wait_for

wait_for('http://127.0.0.1:8020/health')
wait_for('http://localhost:8080/')
print('Local browser routes ready.')
