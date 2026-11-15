"""Shared service diagnostics using the existing bounded telemetry adapters.

Only explicitly tagged events are exported; ordinary log messages, HTTP bodies,
credentials, conversation text and exception messages never enter this sink.
"""
from contextlib import asynccontextmanager
import logging
from time import perf_counter
from uuid import uuid4

from card_servicing.config import InfrastructureSettings
from card_servicing.telemetry import MongoTelemetry, NullTelemetry, StdoutTelemetry


class DiagnosticHandler(logging.Handler):
    def __init__(self, sink, service):
        super().__init__()
        self.sink, self.service = sink, service
        self.names = {'agent': {'card_chat.main', 'card_chat.provider'},
                      'mcp': {'card_chat.mcp_server'}}.get(service, set())

    def emit(self, record):
        if record.name not in self.names:
            return
        event = getattr(record, 'telemetry_event', None)
        fields = getattr(record, 'telemetry_fields', None)
        if event in ('model_usage', 'workflow_usage', 'tool_execution') and isinstance(fields, dict):
            from card_chat.security import trace
            self.sink.emit('log', event, **{**fields, 'service': self.service,
                                          'level': record.levelname, 'trace_id': trace.get()})


def instrument(app, config, service):
    """Wrap each app's existing lifespan and retain its established orchestration."""
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        selected = InfrastructureSettings.from_mapping(
            {**config, 'TELEMETRY_SINK': config.get('TELEMETRY_SINK', 'none')}, select_store=False)
        sinks = {'mongo': MongoTelemetry, 'stdout': StdoutTelemetry}
        sink = (NullTelemetry() if selected.telemetry_sink == 'none'
                else sinks[selected.telemetry_sink](selected.telemetry_config))
        application.state.telemetry = sink
        handler = DiagnosticHandler(sink, service)
        logger = logging.getLogger('card_chat')
        sink.start()
        logger.addHandler(handler)
        try:
            async with original(application):
                yield
        finally:
            logger.removeHandler(handler)
            sink.close()

    app.router.lifespan_context = lifespan

    @app.middleware('http')
    async def request_trace(request, call_next):
        started = perf_counter()
        code, tid = 500, uuid4().hex
        supplied = request.headers.get('X-Correlation-ID', '')
        if len(supplied) == 32 and all(c in '0123456789abcdef' for c in supplied):
            tid = supplied
        try:
            response = await call_next(request)
            code = response.status_code
            tid = response.headers.get('X-Correlation-ID', tid)
            return response
        finally:
            sink = getattr(app.state, 'telemetry', None)
            if sink:
                route = request.scope.get('route')
                sink.emit('trace', 'http_request', service=service, trace_id=tid,
                          span_id=uuid4().hex[:16], method=request.method,
                          route=getattr(route, 'path', 'unmatched'), status_code=code,
                          duration_ms=round((perf_counter() - started) * 1000, 3))
