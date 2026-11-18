"""Shared service diagnostics using the existing bounded telemetry adapters.

Only explicitly tagged events are exported; ordinary log messages, HTTP bodies,
credentials, conversation text and exception messages never enter this sink.
"""
from contextlib import asynccontextmanager
import logging
from time import perf_counter
from uuid import uuid4

from card_servicing.config import InfrastructureSettings
from card_servicing.telemetry import AzureBlobTelemetry, MongoTelemetry, NullTelemetry, StdoutTelemetry


class DiagnosticHandler(logging.Handler):
    def __init__(self, sink, service):
        super().__init__()
        self.sink, self.service = sink, service
        self.names = {'agent': {'card_chat.main', 'card_chat.provider', 'card_chat.agent',
                                'card_chat.orchestration.supervisor', 'card_chat.orchestration.specialists',
                                'card_chat.orchestration.graph', 'card_chat.pii.gateway'},
                      'mcp': {'card_chat.mcp_server'},
                      'fincrime-agent': {'card_chat.provider', 'card_chat.fincrime_agent'}}.get(service, set())

    def emit(self, record):
        if record.name not in self.names:
            return
        event = getattr(record, 'telemetry_event', None)
        fields = getattr(record, 'telemetry_fields', None)
        supported_events = (
            'model_usage', 'workflow_usage', 'tool_execution',
            'supervisor_decision', 'agent_handoff', 'workflow_transition', 'pii_sanitization'
        )
        if event in supported_events and isinstance(fields, dict):
            from card_chat.security import trace
            self.sink.emit('log', event, **{**fields, 'service': self.service,
                                          'level': record.levelname, 'trace_id': trace.get()})


def emit_agent_trace(event: str, **fields):
    """Safely log an agent telemetry event without exposing raw customer details."""
    logger = logging.getLogger('card_chat.agent')
    # Sanitize fields so no raw PII is exported
    sanitized_fields = {}
    for k, v in fields.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            sanitized_fields[k] = v
        elif isinstance(v, (list, tuple)):
            sanitized_fields[k] = [str(item) for item in v]
        elif isinstance(v, dict):
            sanitized_fields[k] = {str(dk): str(dv) for dk, dv in v.items()}
    logger.info(
        "Agent telemetry: %s", event,
        extra={'telemetry_event': event, 'telemetry_fields': sanitized_fields}
    )


def instrument(app, config, service):
    """Wrap each app's existing lifespan and retain its established orchestration."""
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        selected = InfrastructureSettings.from_mapping(
            {**config, 'TELEMETRY_SINK': config.get('TELEMETRY_SINK', 'none')}, select_store=False)
        sinks = {'mongo': MongoTelemetry, 'azure_blob': AzureBlobTelemetry, 'stdout': StdoutTelemetry}
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
