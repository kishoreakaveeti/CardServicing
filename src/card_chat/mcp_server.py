"""Catalogue-driven official MCP Streamable HTTP server."""
import asyncio
import logging
import json
from time import perf_counter
from urllib.parse import urlsplit, unquote, quote

import anyio
import httpx
from jsonschema import Draft202012Validator, FormatChecker
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from contextlib import asynccontextmanager
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from fastapi import FastAPI, HTTPException
from starlette.routing import Route
from starlette.responses import JSONResponse
from mcp.types import Tool

from api_client.client import ApiClient
from card_chat.catalogue import load_catalogue
from card_chat.config import settings
from card_chat.security import Security, protect, principal, trace

logger = logging.getLogger(__name__)

DEFAULT_AGENT_TOOLS = {
    "cards_agent": ["create_card", "issue_card", "replace_card", "view_cards", "view_accounts", "create_account", "logout"],
    "fincrime_agent": ["block_card", "unblock_card"]
}


def get_agent_tools_policy(config=None, catalogue=None):
    config = config or {}
    raw = config.get("MCP_AGENT_TOOLS")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            pass
    elif isinstance(raw, dict):
        return raw
    if catalogue is None:
        try:
            catalogue = load_catalogue(config)
        except Exception:
            catalogue = None
    if catalogue:
        policy = {'cards_agent': [], 'fincrime_agent': []}
        for spec in catalogue.values():
            agents = getattr(spec.entry, 'agents', None)
            if agents:
                for ag in agents:
                    policy.setdefault(ag, []).append(spec.name)
            elif spec.name in ('block_card', 'unblock_card'):
                policy['fincrime_agent'].append(spec.name)
            else:
                policy['cards_agent'].append(spec.name)
        if 'logout' not in policy['cards_agent']:
            policy['cards_agent'].append('logout')
        return policy
    return DEFAULT_AGENT_TOOLS


def get_tools_for_agent(agent_name: str, config=None, catalogue=None):
    policy = get_agent_tools_policy(config, catalogue)
    return policy.get(agent_name, [])


def execute(spec, arguments, key, base_url, execution_headers=None):
    started = perf_counter()
    outcome = 'error'
    try:
        values = spec.validate(arguments)
        headers = dict(execution_headers or {})
        if spec.entry.idempotency_header:
            if not isinstance(key, str) or not key or len(key) > 128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-' for c in key):
                raise ValueError('Execution key required')
            headers[spec.entry.idempotency_header] = key
        with ApiClient(base_url) as client:
            status, data = client.execute_operation(
                spec.method, spec.path,
                path_parameters={k: values[k] for k in spec.path_fields},
                body={k: v for k, v in values.items() if k in spec.body_fields} if spec.body_fields is not None else None,
                execution_headers=headers)
            issuance_outcome = client.last_response_headers.get('Card-Issuance-Outcome')
        schema = spec.responses.get(status)
        if schema is None or not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(data):
            return {'ok': False, 'error': 'API returned an invalid response.'}
        outcome = 'success'
        result = {'ok': True, 'result': data}
        if issuance_outcome in ('created', 'already_exists', 'additional_created', 'replacement_created'):
            result['issuance_outcome'] = issuance_outcome
        return result
    except httpx.TimeoutException:
        return {'ok': False, 'error': 'Card API timed out; the outcome may be uncertain.'}
    except httpx.HTTPStatusError as exc:
        return {'ok': False, 'error': f'Card API returned HTTP {exc.response.status_code}.'}
    except (httpx.RequestError, ValueError):
        return {'ok': False, 'error': 'API unavailable or invalid execution arguments.'}
    finally:
        metrics = {'tool': spec.name, 'duration_ms': round((perf_counter() - started) * 1000, 1),
                   'outcome': outcome}
        logger.warning('tool=%s trace_id=%s duration_ms=%.1f outcome=%s', spec.name,
                       trace.get() or '-', metrics['duration_ms'], outcome,
                       extra={'telemetry_event': 'tool_execution', 'telemetry_fields': metrics})


def create_server(config):
    catalogue = load_catalogue(config)
    server = Server('Card servicing')

    @server.list_resources()
    async def list_resources():
        return []  # Customer-scoped fact URIs are addressed only by the trusted gateway.

    @server.read_resource()
    async def read_policy_facts(uri):
        parsed = urlsplit(str(uri))
        if parsed.scheme != 'policy-facts' or parsed.netloc != 'accounts':
            raise ValueError('Unknown resource')
        account = unquote(parsed.path.lstrip('/'))
        security = Security(config)
        from card_chat.security import delegation
        if not security.enabled:
            raise HTTPException(403, 'Verified customer delegation required')
        customer = security.customer(delegation.get(), 'mcp')
        if account not in customer.get('accounts', []) or 'tools:execute' not in customer.get('permissions', []):
            raise HTTPException(403, 'Account is not authorized')
        # Read only signed customer card selections. Do not trust account/card
        # identifiers supplied by a model or disclose another customer's records.
        cards = [c for c in customer.get('cards', []) if isinstance(c, dict)
                 and c.get('account_number') == account]
        blocked = []
        async with httpx.AsyncClient(timeout=15) as client:
            headers = await security.headers()
            base = config.get('FINCRIME_API_BASE_URL', 'http://127.0.0.1:8025').rstrip('/')
            for card in cards:
                response = await client.get(base + '/fincrime/cards/' + quote(str(card['id']), safe=''), headers=headers)
                if response.status_code == 404:
                    if card.get('status') == 'blocked':
                        # Preserve the restriction without inventing unblock eligibility.
                        blocked.append(dict(card))
                    continue
                response.raise_for_status()
                record = response.json()
                if str(record.get('card_id')) != str(card['id']):
                    raise ValueError('Invalid FinCrime card facts')
                if record.get('status') not in ('blocked', 'unblocked', 'created'):
                    raise ValueError('Invalid FinCrime card status')
                if record.get('status') == 'blocked':
                    blocked.append({**card, 'reason': record.get('reason', ''), 'status': 'blocked'})
        compromised = any(any(term in str(c.get('reason', '')).lower()
            for term in ('lost', 'stolen', 'fraud', 'compromised', 'destroyed')) for c in blocked)
        frozen = any(any(term in str(c.get('reason', '')).lower()
            for term in ('freeze', 'frozen', 'temporary')) for c in blocked)
        facts = {'account_number': account, 'has_blocked_card': bool(blocked),
                 'blocked_cards': blocked, 'is_temporary_freeze': frozen,
                 'is_permanently_compromised': compromised}
        return [ReadResourceContents(content=json.dumps(facts), mime_type='application/json')]

    @server.list_tools()
    async def list_tools():
        return [Tool(name=spec.name, description=spec.entry.description,
                     inputSchema=spec.input_schema) for spec in catalogue.values()]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        # Validate here so SDK validation messages never echo customer values.
        if name not in catalogue:
            return {'ok': False, 'error': 'Unknown configured tool.'}
        # Discovery is service-authorized; invocation additionally requires a
        # verified customer assertion. Neither credential is a tool argument.
        security = Security(config)
        if security.enabled:
            from card_chat.security import delegation
            customer = security.customer(delegation.get(), 'mcp')
            if not set(catalogue[name].entry.permissions) <= set(customer.get('permissions', [])):
                raise HTTPException(403, 'Tool is not permitted for this customer')
        meta = server.request_context.meta
        meta_dict = meta.model_dump() if meta is not None else {}
        key = meta_dict.get('idempotency_key')
        caller_agent = meta_dict.get('agent')
        if caller_agent:
            policy = get_agent_tools_policy(config, catalogue)
            if caller_agent in policy and name not in policy[caller_agent]:
                raise HTTPException(403, f"Tool '{name}' is not permitted for agent '{caller_agent}'")
        headers = await security.headers()
        if name == 'replace_card' and meta_dict.get('replacement_context'):
            replacement = meta_dict['replacement_context']
            headers['X-Replacement-Context'] = json.dumps(replacement)
            if replacement.get('fincrime_approval'):
                headers['X-Approved-Case-Execution'] = '1'
                headers['X-Approved-Customer-ID'] = replacement.get('customer_id', '')
                headers['X-Approved-Account'] = replacement.get('account_number', '')
        return await anyio.to_thread.run_sync(execute, catalogue[name], arguments, key,
                    config.get('CARD_API_BASE_URL', 'http://127.0.0.1:8010'), headers)

    return server


def create_app(config=None):
    config = settings() if config is None else config
    server = create_server(config)
    allowed_hosts = ['mcp:8000', 'mcp-server:8000', 'card-servicing-mcp:8000', '127.0.0.1:*', 'localhost:*']
    for url_key in ('MCP_URL', 'MCP_RESOURCE'):
        if config.get(url_key):
            from urllib.parse import urlsplit
            netloc = urlsplit(config[url_key]).netloc
            if netloc and netloc not in allowed_hosts:
                allowed_hosts.append(netloc)
    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[]))

    @asynccontextmanager
    async def lifespan(app):
        if not config.get('OIDC_ISSUER') and config.get('ALLOW_INSECURE_LOCAL') != '1':
            raise RuntimeError('Configure OIDC before starting MCP; local tests require ALLOW_INSECURE_LOCAL=1')
        async with manager.run():
            yield

    app = FastAPI(title='Card MCP', lifespan=lifespan)
    protect(app, config, 'mcp', 'tools:execute', 'agent-service', customer=False)
    from card_chat.observability import instrument
    instrument(app, config, 'mcp')
    # Mount the ASGI endpoint as an exact route (no trailing-slash redirect).
    class Endpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)
    app.router.routes.append(Route('/mcp', endpoint=Endpoint(),
                                   methods=['GET', 'POST', 'DELETE']))

    @app.get('/health')
    async def health():
        return {'status': 'ok'}

    @app.get('/mcp/agents/{agent_name}/tools')
    async def agent_tools_endpoint(agent_name: str):
        catalogue = load_catalogue(config)
        policy = get_agent_tools_policy(config, catalogue)
        allowed = set(policy.get(agent_name, []))
        return {
            'agent': agent_name,
            'tools': [spec.definition() for spec in catalogue.values() if spec.name in allowed]
        }

    @app.get('/.well-known/oauth-protected-resource')
    async def resource_metadata():
        return {'resource': config.get('MCP_RESOURCE', 'http://mcp:8000/mcp'),
                'authorization_servers': [config.get('OIDC_ISSUER', '')],
                'scopes_supported': ['tools:execute'], 'bearer_methods_supported': ['header']}

    return app


app = create_app()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8030)
