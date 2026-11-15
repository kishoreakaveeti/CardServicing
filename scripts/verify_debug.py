"""DAP smoke: attach, bind a mapped health breakpoint, continue and safely detach.

Run against stack.ps1 -DebugComponent All with no VS Code debugger attached.
Docker health probes supply the requests; no internal HTTP port is published.
"""
import json
from pathlib import Path
import socket
import argparse

ROOT = Path(__file__).resolve().parents[1]


class DAP:
    def __init__(self, port):
        self.socket = socket.create_connection(('127.0.0.1', port), timeout=20)
        self.stream = self.socket.makefile('rb')
        self.seq = 0
        self.pending = []

    def send(self, command, arguments=None):
        self.seq += 1
        body = json.dumps({'seq': self.seq, 'type': 'request', 'command': command,
                           'arguments': arguments or {}}).encode()
        self.socket.sendall(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
        return self.seq

    def wait(self, predicate):
        for index, item in enumerate(self.pending):
            if predicate(item):
                return self.pending.pop(index)
        while True:
            headers = {}
            while (line := self.stream.readline()) != b'\r\n':
                if not line:
                    raise RuntimeError('Debugger disconnected')
                key, value = line.decode().split(':', 1)
                headers[key.lower()] = value.strip()
            item = json.loads(self.stream.read(int(headers['content-length'])))
            if predicate(item):
                return item
            self.pending.append(item)

    def response(self, sequence):
        value = self.wait(lambda item: item.get('type') == 'response' and item.get('request_seq') == sequence)
        assert value.get('success'), value.get('message', 'DAP request failed')
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=['Experience', 'Agent', 'MCP', 'Cards'])
    parser.add_argument('--startup', action='store_true', help='Break on Agent discovery or MCP catalogue loading before HTTP starts')
    args = parser.parse_args()
    if args.startup and args.component not in ('Agent', 'MCP'):
        parser.error('--startup requires --component Agent or MCP')
    for name, port, relative in [('Experience', 5681, 'card_chat/experience.py'),
                                 ('Agent', 5682, 'card_chat/main.py'),
                                 ('MCP', 5683, 'card_chat/mcp_server.py'),
                                 ('Cards', 5684, 'card_servicing/main.py')]:
        if args.component and args.component != name:
            continue
        client = DAP(port)
        try:
            client.response(client.send('initialize', {'adapterID': 'python', 'pathFormat': 'path',
                'linesStartAt1': True, 'columnsStartAt1': True, 'supportsVariableType': True}))
            attach = client.send('attach', {'justMyCode': True, 'subProcess': False, 'clientOS': 'Windows',
                'pathMappings': [{'localRoot': str(ROOT / 'src'), 'remoteRoot': '/app/src'}]})
            client.wait(lambda item: item.get('event') == 'initialized')
            if args.startup and name == 'Agent':
                relative = 'card_chat/gateway.py'
            path = ROOT / 'src' / relative
            lines = path.read_text(encoding='utf-8').splitlines()
            if args.startup:
                line = next(index + 1 for index, line in enumerate(lines) if 'catalogue = load_catalogue(config)' in line)
            else:
                needle = 'def readiness(' if name == 'Cards' else 'def health('
                health = next(index for index, line in enumerate(lines) if needle in line)
                line = next(index + 1 for index in range(health + 1, len(lines))
                            if lines[index].lstrip().startswith(('return ', "journal.db.execute", "db.execute")))
            result = client.response(client.send('setBreakpoints', {'source': {'path': str(path)},
                'breakpoints': [{'line': line}]}))
            assert result['body']['breakpoints'][0]['verified'], f'{name}: {result["body"]["breakpoints"]}'
            client.response(client.send('configurationDone'))
            client.response(attach)
            stopped = client.wait(lambda item: item.get('event') == 'stopped')
            assert stopped['body']['reason'] == 'breakpoint'
            frames = client.response(client.send('stackTrace', {'threadId': stopped['body']['threadId']}))
            source = frames['body']['stackFrames'][0]['source']['path']
            assert Path(source).resolve() == path.resolve(), f'{name}: source mapping mismatch'
            client.response(client.send('setBreakpoints', {'source': {'path': str(path)}, 'breakpoints': []}))
            client.response(client.send('continue', {'threadId': stopped['body']['threadId']}))
            print(f'PASS: {name} attach, mapped breakpoint, pause, continue', flush=True)
        finally:
            try:
                client.response(client.send('disconnect', {'terminateDebuggee': False}))
            except (OSError, RuntimeError):
                pass
            finally:
                client.stream.close()
                client.socket.close()
    print('PASS: all debugger sessions detached without terminating services', flush=True)


if __name__ == '__main__':
    main()
