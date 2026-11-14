from dataclasses import dataclass, field
import os
import ipaddress
from pathlib import Path
import re

from dotenv import dotenv_values

@dataclass(frozen=True)
class CardSettings:
    """Extension point for future card origination policy."""
    pass


class ConfigurationError(ValueError):
    """Invalid startup settings; messages name fields, never supplied values."""


ROOT = Path(__file__).resolve().parents[2]


def load_config(env_file: Path | None = None) -> dict:
    return {**dotenv_values(env_file if env_file is not None else ROOT / '.env'), **os.environ}


def required_text(config, key, default=None):
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ConfigurationError(f'{key} must be nonblank text without control characters')
    return value


@dataclass(frozen=True)
class InfrastructureSettings:
    card_store: str = 'sqlite'
    telemetry_sink: str = 'mongo'
    sqlite_path: Path | None = None
    telemetry_config: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_mapping(cls, config: dict, *, select_store=True, select_telemetry=True):
        # Skip settings for explicitly injected instances: configuration cannot replace them.
        store = config.get('CARD_STORE', 'sqlite') if select_store else 'injected'
        sink = config.get('TELEMETRY_SINK', 'mongo') if select_telemetry else 'injected'
        if select_store and store != 'sqlite':
            raise ConfigurationError('CARD_STORE must be sqlite')
        if select_telemetry and sink not in ('mongo', 'stdout', 'none'):
            raise ConfigurationError('TELEMETRY_SINK must be mongo, stdout or none')
        path = None
        if select_store:
            raw_path = required_text(config, 'SQLITE_PATH', 'data/cards.db')
            try:
                path = Path(raw_path)
                if not path.is_absolute():
                    path = ROOT / path
            except (ValueError, OSError):
                raise ConfigurationError('SQLITE_PATH must be a valid filesystem path') from None
        telemetry_config = {}
        if sink in ('mongo', 'stdout'):
            # No model key or unrelated infrastructure secrets are handed to telemetry.
            telemetry_config = {key: config[key] for key in ('ACCOUNT_HASH_KEY', 'ACCOUNT_HASH_KEY_VERSION')
                                if config.get(key) is not None}
        if sink == 'mongo':
            host = required_text(config, 'MONGODB_HOST', '127.0.0.1')
            try:
                ipaddress.ip_address(host)
            except ValueError:
                labels = host.rstrip('.').split('.')
                if len(host) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                                              for label in labels):
                    raise ConfigurationError('MONGODB_HOST must be a hostname or IP address, not a URI') from None
            try:
                port = int(config.get('MONGODB_PORT', '27018'))
                if not 1 <= port <= 65535:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ConfigurationError('MONGODB_PORT must be an integer between 1 and 65535') from None
            database = required_text(config, 'MONGODB_DATABASE', 'card_servicing')
            if any(c in database for c in '/\\. "$*<>:|?') or len(database.encode()) > 63:
                raise ConfigurationError('MONGODB_DATABASE must be a valid database name')
            telemetry_config.update({
                'MONGODB_HOST': host, 'MONGODB_PORT': port, 'MONGODB_DATABASE': database,
                'MONGO_USER': required_text(config, 'MONGO_USER'),
                'MONGO_PASSWORD': required_text(config, 'MONGO_PASSWORD'),
            })
        return cls(store, sink, path, telemetry_config)
