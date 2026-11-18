"""Bounded dependency probes at the process boundary; never replay writes."""
import time
from urllib.request import urlopen


def wait_for_dependencies(urls, timeout=60, *, probe=None, clock=time.monotonic, sleep=time.sleep):
    if not urls:
        return
    deadline = clock() + timeout
    pending = list(urls)
    def http_probe(url, remaining):
        with urlopen(url, timeout=min(2, remaining)) as response:
            if response.status != 200:
                raise OSError('not ready')
    probe = probe or http_probe
    while pending:
        for url in pending[:]:
            remaining = deadline - clock()
            if remaining <= 0:
                raise RuntimeError('Startup dependencies unavailable within configured deadline')
            try:
                probe(url, remaining)
                pending.remove(url)
            except Exception:
                pass
        if pending:
            remaining = deadline - clock()
            if remaining <= 0:
                raise RuntimeError('Startup dependencies unavailable within configured deadline')
            sleep(min(1, remaining))
