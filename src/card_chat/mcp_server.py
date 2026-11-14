"""Catalogue-driven official MCP stdio server; no operation-specific routing."""
import asyncio
import logging
from time import perf_counter

import anyio
import httpx
from jsonschema import Draft202012Validator, FormatChecker
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

from api_client.client import ApiClient
from card_chat.catalogue import load_catalogue
from card_chat.config import settings

logger = logging.getLogger(__name__)


def execute(spec, arguments, key, base_url):
    started = perf_counter()
    outcome = 'error'
    try:
        values = spec.validate(arguments)
        headers = {}
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
        logger.warning('tool=%s duration_ms=%.1f outcome=%s', spec.name,
                       (perf_counter() - started) * 1000, outcome)


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
        meta = server.request_context.meta
        key = meta.model_dump().get('idempotency_key') if meta is not None else None
        return await anyio.to_thread.run_sync(execute, catalogue[name], arguments, key,
                    config.get('CARD_API_BASE_URL', 'http://127.0.0.1:8010'))

    return server


async def main():
    server = create_server(settings())
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
