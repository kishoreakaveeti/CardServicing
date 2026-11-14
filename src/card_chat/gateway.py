from contextlib import asynccontextmanager
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from card_chat.catalogue import load_catalogue
from card_chat.config import settings


class Gateway:
    def __init__(self, session, discovered, catalogue):
        self.session = session
        self.catalogue = catalogue
        self.tools = {tool.name: {'name': tool.name, 'description': tool.description or '',
                                 'input_schema': tool.inputSchema} for tool in discovered}
        if set(self.tools) != set(catalogue):
            raise RuntimeError('Discovered MCP tools do not match the configured catalogue')
        for name, spec in catalogue.items():
            if self.tools[name] != spec.definition():
                raise RuntimeError('Discovered MCP tool differs from the compiled catalogue: ' + name)

    async def execute(self, name, arguments, key=None):
        self.catalogue[name].validate(arguments)
        meta = {'idempotency_key': key} if key else None
        result = await self.session.call_tool(name, arguments, meta=meta)
        if result.isError or not result.structuredContent:
            return {'ok': False, 'error': 'MCP execution failed; the outcome may be uncertain.'}
        return result.structuredContent


@asynccontextmanager
async def connect():
    config = settings()
    catalogue = load_catalogue(config)
    # Only the chat adapter needs the model credential, not the MCP child.
    env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
    for key in ('TOOL_CATALOGUE_PATH', 'CARD_API_BASE_URL'):
        if config.get(key):
            env[key] = config[key]
    params = StdioServerParameters(command=sys.executable,
                                  args=['-m', 'card_chat.mcp_server'], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            yield Gateway(session, tools, catalogue)
