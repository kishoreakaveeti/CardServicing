from typing import Annotated
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from card_servicing.models import Item, ItemCreate
from card_servicing.store import ItemStore
from card_servicing.config import CardSettings, InfrastructureSettings, load_config
from card_servicing.cards import router as cards_router
from card_servicing.store import CardStore
from card_servicing.persistence import SQLiteCardStore
from card_servicing.telemetry import AzureBlobTelemetry, MongoTelemetry, NullTelemetry, StdoutTelemetry
from card_servicing.ports import CardStorePort, StorageUnavailable, TelemetryPort


def get_item_store(request: Request) -> ItemStore:
    return request.app.state.item_store


def configured_adapters(card_store, telemetry):
    settings = InfrastructureSettings.from_mapping(load_config(),
                select_store=card_store is None, select_telemetry=telemetry is None)
    if card_store is None:
        card_store = SQLiteCardStore(str(settings.sqlite_path))
    if telemetry is None:
        sinks = {'mongo': MongoTelemetry, 'azure_blob': AzureBlobTelemetry, 'stdout': StdoutTelemetry}
        telemetry = (NullTelemetry() if settings.telemetry_sink == 'none'
                     else sinks[settings.telemetry_sink](settings.telemetry_config))
    return card_store, telemetry


def create_app(card_settings: CardSettings | None = None, *,
               card_store: CardStorePort | None = None, telemetry: TelemetryPort | None = None,
               local_infrastructure: bool = False, security_config=None) -> FastAPI:
    injected_adapters = card_store, telemetry
    if not local_infrastructure:
        card_store = card_store if card_store is not None else CardStore()
        telemetry = telemetry if telemetry is not None else NullTelemetry()

    @asynccontextmanager
    async def lifespan(application):
        nonlocal card_store, telemetry
        if local_infrastructure:
            card_store, telemetry = configured_adapters(*injected_adapters)
        application.state.card_store = card_store
        application.state.telemetry = telemetry
        telemetry.start()
        try:
            try:
                card_store.initialize()
                if isinstance(card_store, SQLiteCardStore):
                    from card_servicing.banking import BankingService
                    application.state.banking = BankingService(card_store)
            except StorageUnavailable as exc:
                telemetry.emit('log', 'card_storage_initialization_failed',
                               level='ERROR', dependency=card_store.dependency_name,
                               error_type=type(exc).__name__)
            application.state.started = True
            yield
        finally:
            application.state.started = False
            telemetry.close()

    application = FastAPI(title="Card Servicing", version="0.1.0", lifespan=lifespan)
    application.state.item_store = ItemStore()
    # Test factories are isolated; the normal entry point uses the configured file.
    application.state.card_store = card_store
    application.state.telemetry = telemetry
    application.state.started = False
    application.state.card_settings = card_settings or CardSettings()
    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': 'Invalid request fields'}, status_code=422)
    @application.exception_handler(StorageUnavailable)
    async def storage_unavailable(request, exc):
        return JSONResponse({'detail': 'Banking storage unavailable'}, status_code=503)
    application.include_router(cards_router)
    from card_servicing.banking import router as banking_router
    application.include_router(banking_router)
    from card_chat.security import protect
    protect(application, security_config if security_config is not None else
            (load_config() if local_infrastructure else {}),
            'cards', 'cards:write', 'mcp-service')

    @application.middleware('http')
    async def trace_request(request, call_next):
        supplied = request.headers.get('X-Correlation-ID', '')
        trace_id = supplied if len(supplied) == 32 and all(c in '0123456789abcdef' for c in supplied) else uuid4().hex
        request.state.trace_id = trace_id
        start = perf_counter()
        code = 500
        try:
            response = await call_next(request)
            code = response.status_code
            response.headers['X-Trace-ID'] = trace_id
            return response
        finally:
            route = request.scope.get('route')
            telemetry.emit('trace', 'http_request', trace_id=trace_id,
                           service='cards',
                           span_id=uuid4().hex[:16], method=request.method,
                           route=getattr(route, 'path', 'unmatched'), status_code=code,
                           duration_ms=round((perf_counter() - start) * 1000, 3))

    @application.get("/health", summary="Liveness (compatibility alias)")
    @application.get("/health/live", summary="Application liveness")
    def health() -> dict[str, str]:
        # No dependency calls: an optional telemetry outage must not restart us.
        return {"status": "ok"}

    @application.get('/health/ready', summary='Readiness to accept card requests',
                     responses={503: {'description': 'Application or card storage not ready'}})
    def readiness(request: Request) -> JSONResponse:
        dependency = getattr(card_store, 'dependency_name', 'card_store')
        available = False
        if application.state.started:
            try:
                available = card_store.check_readiness()
            except StorageUnavailable as exc:
                # Report only exception type; filesystem paths and SQL details
                # belong neither in probe responses nor in diagnostic payloads.
                telemetry.emit('log', 'card_readiness_failed', level='WARNING',
                               trace_id=request.state.trace_id, dependency=dependency,
                               error_type=type(exc).__name__)
        return JSONResponse(
            status_code=200 if available else 503,
            content={'status': 'ready' if available else 'not_ready',
                     'checks': {dependency: 'ok' if available else 'unavailable'}},
            headers={'Cache-Control': 'no-store'},
        )

    @application.get("/items", response_model=list[Item])
    def list_items(
        store: Annotated[ItemStore, Depends(get_item_store)],
    ) -> list[Item]:
        return store.list()

    @application.get("/items/{item_id}", response_model=Item)
    def get_item(
        item_id: int,
        store: Annotated[ItemStore, Depends(get_item_store)],
    ) -> Item:
        item = store.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Item not found")
        return item

    @application.post(
        "/items", response_model=Item, status_code=status.HTTP_201_CREATED
    )
    def create_item(
        item: ItemCreate,
        store: Annotated[ItemStore, Depends(get_item_store)],
    ) -> Item:
        return store.create(item)

    @application.put("/items/{item_id}", response_model=Item)
    def update_item(
        item_id: int,
        item: ItemCreate,
        store: Annotated[ItemStore, Depends(get_item_store)],
    ) -> Item:
        updated = store.update(item_id, item)
        if updated is None:
            raise HTTPException(status_code=404, detail="Item not found")
        return updated

    @application.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_item(
        item_id: int,
        store: Annotated[ItemStore, Depends(get_item_store)],
    ) -> Response:
        if not store.delete(item_id):
            raise HTTPException(status_code=404, detail="Item not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return application


app = create_app(local_infrastructure=True)
