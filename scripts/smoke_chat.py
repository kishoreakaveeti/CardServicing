"""Exercise chat endpoints -> real MCP stdio -> a separately running Cards API.

Default: stub model, no cloud calls. --live uses configured Anthropic credentials.
--lose-first-response verifies retry after the API has committed a creation.
All customer details are synthetic. Run from the repository root.
"""
import argparse
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient

from card_chat.config import settings
from card_chat.gateway import Gateway
from card_chat.main import create_app
from card_chat.provider import make_model
from card_chat.turns import ProposedToolCall


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--lose-first-response', action='store_true')
    args = parser.parse_args()
    os.environ['CARD_API_BASE_URL'] = args.api_url
    if args.live:
        model = make_model(settings())
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
        from test_chat import StubModel, BODY
        model = StubModel()
        model.outputs = [ProposedToolCall('create_card', BODY)]
    original = Gateway.execute
    calls, results = [], []

    async def capture(gateway, name, body, key=None):
        result = await original(gateway, name, body, key)
        calls.append((name, body.copy(), key))
        results.append(result)
        if args.lose_first_response and len(calls) == 1 and result.get('ok'):
            raise TimeoutError('Synthetic response loss after creation')
        return result

    Gateway.execute = capture
    try:
        with TestClient(create_app(model=model)) as client:
            start = client.post('/conversations').json()
            path = '/conversations/' + start['id']
            review = client.post(path + '/messages', json={'text':
                'Create a new simulated card for my existing account. My given name is Alex, '
                'my user name is alex.synthetic, and my account number is the string 0012345678.'}).json()
            if not review.get('review'):
                raise RuntimeError('No complete review returned; inspect model configuration and chat response locally')
            assert not calls, 'MCP must not execute before confirmation'
            confirmation = {'review': review['review']}
            first = client.post(path + '/confirm', json=confirmation).json()
            if args.lose_first_response:
                assert 'retry' in first['message']
                first = client.post(path + '/confirm', json=confirmation).json()
                assert len(calls) == 2 and calls[0] == calls[1]
                assert results[0] == results[1]
            assert 'SIM-' in first['message'], first['message']
            count = len(calls)
            duplicate = client.post(path + '/confirm', json=confirmation).json()
            assert duplicate['message'] == first['message']
            assert len(calls) == count, 'Completed duplicate must use the saved result'
            print('PASS: review -> confirmed chat -> MCP -> HTTP creation -> identical retry/duplicate result')
            print('Model:', 'live Anthropic' if args.live else 'stub')
            print('Reference:', results[-1]['result']['id'])
    finally:
        Gateway.execute = original


if __name__ == '__main__':
    main()
