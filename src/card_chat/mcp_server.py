"""Catalogue-driven official MCP Streamable HTTP server."""
import asyncio
import logging
from time import perf_counter

import anyio
import httpx
from jsonschema import Draft202012Validator, FormatChecker
from mcp.server import Server
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
        schema = spec.responses.get(status)
        if schema is None or not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(data):
            return {'ok': False, 'error': 'API returned an invalid response.'}
        outcome = 'success'
        return {'ok': True, 'result': data}
    except httpx.TimeoutException:
        return {'ok': False, 'error': 'Card API timed out; the outcome may be uncertain.'}
    except httpx.HTTPStatusError as exc:
        return {'ok': False, 'error': f'Card API returned HTTP {exc.response.status_code}.'}
    except (httpx.RequestError, ValueError):
        return {'ok': False, 'error': 'API unavailable or invalid execution arguments.'}
    finally:
        logger.warning('tool=%s trace_id=%s duration_ms=%.1f outcome=%s', spec.name,
                       trace.get() or '-', (perf_counter() - started) * 1000, outcome)


def create_server(config):
    catalogue = load_catalogue(config)
    server = Server('Card servicing')

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
        key = meta.model_dump().get('idempotency_key') if meta is not None else None
        headers = await security.headers()
        return await anyio.to_thread.run_sync(execute, catalogue[name], arguments, key,
                    config.get('CARD_API_BASE_URL', 'http://127.0.0.1:8010'), headers)

    return server


def create_app(config=None):
    config = settings() if config is None else config
    server = create_server(config)
    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=['mcp:8000', '127.0.0.1:*', 'localhost:*'],
            allowed_origins=[]))

    @asynccontextmanager
    async def lifespan(app):
        if not config.get('OIDC_ISSUER') and config.get('ALLOW_INSECURE_LOCAL') != '1':
            raise RuntimeError('Configure OIDC before starting MCP; local tests require ALLOW_INSECURE_LOCAL=1')
        async with manager.run():
            yield

    app = FastAPI(title='Card MCP', lifespan=lifespan)
    protect(app, config, 'mcp', 'tools:execute', 'agent-service', customer=False)
    # Mount the ASGI endpoint as an exact route (no trailing-slash redirect).
    class Endpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)
    app.router.routes.append(Route('/mcp', endpoint=Endpoint(),
                                   methods=['GET', 'POST', 'DELETE']))

    @app.get('/health')
    async def health():
        return {'status': 'ok'}

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
