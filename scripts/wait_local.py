"""Signal browser readiness while the compound launches its Python debuggers."""
import argparse
import time
import urllib.error
import urllib.request

ROUTES = (
    ('Identity', 'http://127.0.0.1:8081/realms/cards/.well-known/openid-configuration'),
    ('Cards API', 'http://127.0.0.1:8010/health/ready'),
    ('MCP server', 'http://127.0.0.1:8030/health'),
    ('Agent API', 'http://127.0.0.1:8021/health'),
    ('Experience API', 'http://127.0.0.1:8020/health'),
    ('Frontend', 'http://127.0.0.1:8080/'),
)


def responding(url):
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def wait_for_routes(routes=ROUTES, timeout=90):
    print('LOCAL_HTTP_WAITING: Waiting for the local debug stack.', flush=True)
    print('This task checks readiness; it does not start services. Use Debug Full Stack (Local).', flush=True)
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        missing = [(name, url) for name, url in routes if not responding(url)]
        if not missing:
            print('LOCAL_HTTP_READY: Open http://localhost:8080', flush=True)
            return
        if missing != previous:
            print('Still waiting for: ' + ', '.join(name for name, _ in missing), flush=True)
            previous = missing
        if time.monotonic() >= deadline:
            endpoints = '\n'.join(f'  {name}: {url}' for name, url in missing)
            raise RuntimeError('Local stack is not ready:\n' + endpoints +
                '\nCheck the corresponding debug terminals for startup errors.' +
                '\nStart the external Identity service first, then select Debug Full Stack (Local).' +
                '\nDebug Anyway only opens a browser; it cannot start the missing services.')
        time.sleep(0.3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=90)
    args = parser.parse_args()
    try:
        wait_for_routes(timeout=args.timeout)
    except RuntimeError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
