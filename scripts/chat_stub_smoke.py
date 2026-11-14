"""Credential-free browser smoke: scripted model, real MCP and isolated REST process.

Run from the repository root. Enter: freeze card; new card;
Alex / alex.synthetic; 0012345678. Then click Create card twice.
This script is a test harness, not a configurable production model provider.
"""
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_chat import StubModel, running_api
from card_chat.main import create_app
from card_chat.turns import CustomerReply, ProposedToolCall
import os

if __name__ == '__main__':
    model = StubModel()
    model.outputs = [
        CustomerReply('This demo supports creating a new card, not freezing it.'),
        CustomerReply('What is your given name?'),
        CustomerReply('What is your existing account number?'),
        ProposedToolCall('create_card', {'given_name': 'Alex', 'user_name': 'alex.synthetic', 'account_number': '0012345678'}),
    ]
    with running_api() as url:
        os.environ['CARD_API_BASE_URL'] = url
        uvicorn.run(create_app(model=model), host='127.0.0.1', port=8020)
