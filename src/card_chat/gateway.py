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

    async def execute(self, name, arguments, key=None, agent=None, replacement_context=None):
        self.catalogue[name].validate(arguments)
        meta = {}
        if key:
            meta['idempotency_key'] = key
        if agent:
            meta['agent'] = agent
        from card_chat.security import replacement_context as ambient_replacement_context
        bound_context = replacement_context or ambient_replacement_context.get()
        if name == 'replace_card' and bound_context:
            meta['replacement_context'] = bound_context
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
    async def session(approved_execution=False):
        headers = await security.headers()
        if approved_execution:
            headers['X-Approved-Case-Execution'] = '1'
        async with httpx.AsyncClient(headers=headers, timeout=120) as client:
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
            async with session(bool(replacement_context and replacement_context.get('fincrime_approval'))) as connected:
                result = await connected.read_resource(AnyUrl(
                    'policy-facts://accounts/' + quote(account_number, safe='')))
                if len(result.contents) != 1 or not hasattr(result.contents[0], 'text'):
                    raise RuntimeError('Invalid policy domain facts')
                return json.loads(result.contents[0].text)

        async def execute_approved_replacement(self, arguments, key, replacement_context):
            """Execute a persisted approval without replaying an expired customer token."""
            catalogue['replace_card'].validate(arguments)
            card_id = quote(str(arguments['card_id']), safe='')
            headers = await security.headers()
            headers.update({
                'Idempotency-Key': key,
                'X-Approved-Case-Execution': '1',
                'X-Approved-Customer-ID': replacement_context.get('customer_id', ''),
                'X-Approved-Account': replacement_context.get('account_number', ''),
                'X-Replacement-Context': json.dumps(replacement_context),
            })
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    config.get('CARD_API_BASE_URL', 'http://cards-api:8000') +
                    f'/banking/cards/{card_id}/replace',
                    json={'reason': arguments.get('reason', 'Approved replacement')}, headers=headers)
            if not response.is_success:
                try:
                    detail = response.json().get('detail')
                except (ValueError, AttributeError):
                    detail = None
                safe_details = {
                    'Country review changed. Please review a new replacement request.',
                    'Approved replacement policy content changed. Please start a new request.',
                    'Approved replacement target requires revalidation',
                    'Approved replacement account ownership changed',
                    'Request key already used for different details',
                }
                error = detail if detail in safe_details else f'Cards API returned HTTP {response.status_code}.'
                return {'ok': False, 'error': error}
            return {'ok': True, 'result': response.json(),
                    'issuance_outcome': response.headers.get('Card-Issuance-Outcome')}

        async def execute(self, name, arguments, key=None, agent=None, replacement_context=None):
            # Fresh destination token and per-job delegation on every request.
            async with session() as connected:
                gw = Gateway(connected, tools, catalogue)
                try:
                    return await gw.execute(name, arguments, key, agent=agent,
                                            replacement_context=replacement_context)
                except TypeError:
                    return await gw.execute(name, arguments, key)

    yield RemoteGateway(None, tools, catalogue)
