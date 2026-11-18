"""Public DNS is an explicit deployment gate, not an assumed certificate prerequisite."""
import json
import socket
import sys
import time


def verify(config, resolve=socket.getaddrinfo):
    for field in ('AppDomain', 'IdentityDomain'):
        addresses = {row[4][0] for row in resolve(config[field], 443, type=socket.SOCK_STREAM)}
        if addresses != {config['PublicIp']}:
            raise RuntimeError(f'{field}: DNS must resolve only to the VM static IPv4 address; remove stale A/AAAA records')


if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1]))
    for attempt in range(60):
        try:
            verify(cfg)
            print('PASS: public DNS matches VM for both HTTPS hostnames')
            break
        except (RuntimeError, OSError):
            if attempt == 59:
                raise SystemExit('DNS is not ready. Set both A records to the printed VM IP, remove conflicting AAAA records, then rerun deploy.ps1.')
            time.sleep(10)
