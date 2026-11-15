from contextlib import asynccontextmanager
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from card_chat.catalogue import load_catalogue
from card_chat.config import settings
from card_chat.security import Security


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
async def connect(config=None):
    config = settings() if config is None else config
    catalogue = load_catalogue(config)
    security = Security(config)
    url = config.get('MCP_URL', 'http://127.0.0.1:8030/mcp')

    @asynccontextmanager
    async def session():
        async with httpx.AsyncClient(headers=await security.headers(), timeout=120) as client:
            async with streamable_http_client(url, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as connected:
                    await connected.initialize()
                    yield connected

    async with session() as connected:
        tools = (await connected.list_tools()).tools

    class RemoteGateway(Gateway):
        async def execute(self, name, arguments, key=None):
            # Fresh destination token and per-job delegation on every request.
            async with session() as connected:
                return await Gateway(connected, tools, catalogue).execute(name, arguments, key)

    yield RemoteGateway(None, tools, catalogue)
