"""Verify the existing telemetry queue reaches Docker Mongo using synthetic events."""
import os
from pathlib import Path
import time
from uuid import uuid4

from dotenv import dotenv_values
from pymongo import MongoClient

from card_servicing.config import InfrastructureSettings
from card_servicing.telemetry import MongoTelemetry


def main():
    root = Path(__file__).resolve().parents[1]
    config = {**dotenv_values(root / '.env'), **dotenv_values(root / '.env.stack'), **os.environ}
    config.update(TELEMETRY_SINK='mongo', MONGODB_HOST=config.get('LOCAL_MONGODB_HOST', '127.0.0.1'),
                  MONGODB_PORT=config.get('MONGODB_HOST_PORT', '27018'))
    settings = InfrastructureSettings.from_mapping(config, select_store=False)
    selected = settings.telemetry_config
    tid = uuid4().hex
    sink = MongoTelemetry(selected)
    sink.start()
    try:
        for kind in ('log', 'trace'):
            sink.emit(kind, 'telemetry_smoke', service='smoke', trace_id=tid)
        with MongoClient(selected['MONGODB_HOST'], selected['MONGODB_PORT'],
                         username=selected['MONGO_USER'], password=selected['MONGO_PASSWORD'],
                         authSource='admin', serverSelectionTimeoutMS=2000) as client:
            database = client[selected['MONGODB_DATABASE']]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if all(database[name].find_one({'trace_id': tid, 'event': 'telemetry_smoke'})
                       for name in ('application_logs', 'request_traces')):
                    print('MongoDB logging verified: application_logs and request_traces received synthetic events.')
                    return
                time.sleep(0.1)
            raise RuntimeError('Synthetic telemetry was not found within ten seconds')
    finally:
        sink.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Provider errors may contain credentials or server addresses.
        raise SystemExit('MongoDB logging verification failed (' + type(exc).__name__ +
                         '). Check that the Mongo service is running and credentials match its volume.') from None
