from contextlib import asynccontextmanager
import json
from urllib.parse import quote
from pydantic import AnyUrl
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

    async def execute(self, name, arguments, key=None, agent=None):
        self.catalogue[name].validate(arguments)
        meta = {}
        if key:
            meta['idempotency_key'] = key
        if agent:
            meta['agent'] = agent
        from card_chat.security import replacement_context
        if name == 'replace_card' and replacement_context.get():
            meta['replacement_context'] = replacement_context.get()
        result = await self.session.call_tool(name, arguments, meta=meta or None)
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
        async def declare_country(self, workflow, context):
            # Separate authenticated fact endpoint; never an LLM-visible tool.
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(config.get('CARD_API_BASE_URL', 'http://cards-api:8000') +
                    '/banking/replacement-context', headers=await security.headers(),
                    json={'workflow': workflow, 'country': context})
                response.raise_for_status()
                return response.json()

        async def policy_facts(self, account_number):
            if not security.enabled:
                return None  # Explicit isolated local mode uses injected verified facts.
            async with session() as connected:
                result = await connected.read_resource(AnyUrl(
                    'policy-facts://accounts/' + quote(account_number, safe='')))
                if len(result.contents) != 1 or not hasattr(result.contents[0], 'text'):
                    raise RuntimeError('Invalid policy domain facts')
                return json.loads(result.contents[0].text)

        async def execute(self, name, arguments, key=None, agent=None):
            # Fresh destination token and per-job delegation on every request.
            async with session() as connected:
                gw = Gateway(connected, tools, catalogue)
                try:
                    return await gw.execute(name, arguments, key, agent=agent)
                except TypeError:
                    return await gw.execute(name, arguments, key)

    yield RemoteGateway(None, tools, catalogue)
