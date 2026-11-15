"""Pipe into the Experience container's Python to verify private boundaries.

This privileged local verification uses the BFF's signing key; never publish this
as a route. Outputs only pass/fail descriptions, never tokens or customer payloads.
"""
import asyncio
import os
import httpx
from card_chat.security import Security


async def main():
    security = Security(dict(os.environ))
    token = await security.service_token()
    customer = security.issue_customer({'sub': 'internal-smoke', 'preferred_username': 'alex.synthetic',
                                        'accounts': ['0012345678']})
    headers = {'Authorization': 'Bearer ' + token, 'X-Customer-Delegation': customer}
    async with httpx.AsyncClient(timeout=30) as client:
        for url in ('http://agent:8000/conversations', 'http://mcp:8000/mcp', 'http://cards:8000/cards'):
            assert (await client.post(url)).status_code == 401
            assert (await client.post(url, headers={'Authorization': 'Bearer invalid'})).status_code == 401
        assert (await client.post('http://mcp:8000/mcp', headers=headers)).status_code == 401
        assert (await client.post('http://cards:8000/cards', headers=headers)).status_code == 401
        assert (await client.post('http://agent:8000/conversations', headers=headers)).status_code == 200
        assert (await client.post('http://agent:8000/conversations', headers={**headers, 'X-Customer-Delegation': 'tampered'})).status_code == 401
    print('PASS: real Keycloak token, invalid credentials on all private hops, wrong audiences, tampered delegation')


asyncio.run(main())
