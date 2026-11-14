from contextlib import asynccontextmanager
from copy import deepcopy
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class Gateway:
    def __init__(self, session, tool):
        self.session = session
        self.tool = tool
        # Execution metadata is available to MCP, never to the model.
        self.schema = deepcopy(tool.inputSchema)
        self.schema['properties'].pop('idempotency_key')
        self.schema['required'].remove('idempotency_key')
        self.schema['additionalProperties'] = False

    async def create(self, body, key):
        result = await self.session.call_tool('create_card', {**body, 'idempotency_key': key})
        if result.isError or not result.structuredContent:
            return {'ok': False, 'error': 'MCP execution failed. Retry this submission.'}
        return result.structuredContent


@asynccontextmanager
async def connect():
    # Do not pass the model credential to the child process.
    env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
    params = StdioServerParameters(command=sys.executable,
                                  args=['-m', 'card_chat.mcp_server'], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            if [tool.name for tool in tools] != ['create_card']:
                raise RuntimeError('Expected only create_card')
            yield Gateway(session, tools[0])
