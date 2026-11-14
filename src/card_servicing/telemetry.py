"""Best-effort asynchronous diagnostics, never a durable card audit ledger."""
import json
import logging
import hashlib
import hmac
import sys
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Event, Thread
from uuid import uuid4

from pymongo import MongoClient

logger = logging.getLogger('card_servicing.telemetry')


def fallback(record: dict) -> None:
    logger.warning(json.dumps(record, default=str))


class NullTelemetry:
    def account_fields(self, instruction) -> dict:
        return {}

    def start(self) -> None:
        pass

    def emit(self, kind: str, event: str, **fields) -> None:
        pass

    def close(self) -> None:
        pass


EVENT_FIELDS = frozenset({
    'trace_id', 'span_id', 'method', 'route', 'status_code', 'duration_ms',
    'level', 'error_type', 'dependency', 'card_id', 'status',
    'account_number_hash', 'account_hash_key_version',
})


def event_record(kind, event, fields):
    # Keep the call-site allowlist enforced at both sink boundaries as well.
    safe = {key: value for key, value in fields.items()
            if key in EVENT_FIELDS and isinstance(value, (str, int, float, bool, type(None)))}
    return {'_id': uuid4().hex, 'timestamp': datetime.now(timezone.utc),
            'kind': kind, 'event': event, **safe}


class AccountDiagnostics:
    def __init__(self, config: dict) -> None:
        self.config = config

    def account_fields(self, instruction) -> dict:
        key = self.config.get('ACCOUNT_HASH_KEY') or ''
        if len(key.encode()) < 32:
            return {}  # Omit accounts if safe hashing is not configured.
        return {
            'account_number_hash': hmac.new(key.encode(), instruction.account_number.encode(), hashlib.sha256).hexdigest(),
            'account_hash_key_version': self.config.get('ACCOUNT_HASH_KEY_VERSION', 'v1'),
        }


class StdoutTelemetry(AccountDiagnostics):
    """Structured API events; never use this sink for MCP protocol stdout."""
    def start(self) -> None:
        pass

    def emit(self, kind: str, event: str, **fields) -> None:
        try:
            print(json.dumps(event_record(kind, event, fields), default=str), file=sys.stdout, flush=True)
        except (OSError, ValueError):
            # Diagnostics remain best effort, including a closed output stream.
            logger.warning('stdout_telemetry_failed')

    def close(self) -> None:
        pass


class MongoTelemetry(AccountDiagnostics):
    def __init__(self, config: dict, queue_size: int = 1000) -> None:
        super().__init__(config)
        self.queue: Queue = Queue(maxsize=queue_size)
        self.stopping = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            raise RuntimeError('Telemetry worker already running')
        self.stopping.clear()
        self.thread = Thread(target=self._run, name='mongo-telemetry', daemon=True)
        self.thread.start()

    def emit(self, kind: str, event: str, **fields) -> None:
        record = event_record(kind, event, fields)
        try:
            self.queue.put_nowait(record)
        except Full:
            fallback({'event': 'telemetry_queue_full', 'record': record})

    def _run(self) -> None:
        client = None
        indexed = False
        try:
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    record = self.queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    if client is None:
                        client = MongoClient(
                            self.config.get('MONGODB_HOST', '127.0.0.1'),
                            int(self.config.get('MONGODB_PORT', 27018)),
                            username=self.config.get('MONGO_USER'), password=self.config.get('MONGO_PASSWORD'),
                            authSource='admin', serverSelectionTimeoutMS=500, connectTimeoutMS=500,
                            socketTimeoutMS=500, retryWrites=False,
                        )
                    database = client[self.config.get('MONGODB_DATABASE', 'card_servicing')]
                    if not indexed:
                        for collection in ('application_logs', 'request_traces'):
                            database[collection].create_index('timestamp', expireAfterSeconds=604800)
                            database[collection].create_index('trace_id')
                        indexed = True
                    collection = 'request_traces' if record['kind'] == 'trace' else 'application_logs'
                    database[collection].insert_one(record)
                except Exception as exc:
                    fallback({'event': 'mongodb_telemetry_failed',
                              'error_type': type(exc).__name__, 'record': record})
                finally:
                    self.queue.task_done()
        finally:
            if client is not None:
                client.close()

    def close(self) -> None:
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                fallback({'event': 'telemetry_shutdown_timeout', 'pending': self.queue.qsize()})
