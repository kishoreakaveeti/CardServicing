"""Token Vault interface and providers for reversible sensitive data tokenization."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import datetime
import logging
import secrets
import string
import threading
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VaultEntry:
    token: str
    raw_value: str
    entity_type: str
    owner: str
    session_id: str
    workflow_id: str
    created_at: datetime.datetime
    expires_at: datetime.datetime

    def is_expired(self) -> bool:
        return datetime.datetime.now(datetime.timezone.utc) > self.expires_at


class TokenVault(ABC):
    """Abstract interface for token vault providers."""

    @abstractmethod
    def tokenize(
        self,
        owner: str,
        session_id: str,
        entity_type: str,
        raw_value: str,
        workflow_id: str = "default",
        ttl_seconds: int = 3600,
    ) -> str:
        """Store raw_value and return an opaque, reversible token scoped to owner and session."""
        pass

    @abstractmethod
    def detokenize(
        self,
        owner: str,
        session_id: str,
        token: str,
    ) -> str | None:
        """Resolve an opaque token to its original value, enforcing owner and session isolation."""
        pass

    @abstractmethod
    def get_token(
        self,
        owner: str,
        session_id: str,
        raw_value: str,
    ) -> str | None:
        """Retrieve existing token for a value within this session if one exists."""
        pass

    @abstractmethod
    def revoke_session(self, owner: str, session_id: str) -> int:
        """Revoke all tokens bound to an owner session."""
        pass

    @abstractmethod
    def cleanup_expired(self) -> int:
        """Purge all expired tokens from the vault."""
        pass


class InMemoryTokenVault(TokenVault):
    """Thread-safe in-memory token vault with TTL expiry and customer/session isolation.

    Limitations:
    - Process restarts purge all tokens; in-flight workflows cannot be resumed across service restarts.
    - Multiple container replicas cannot share state without sticky sessions or external persistence.
    - Memory footprint grows with active sessions if cleanup is not periodically triggered.
    """

    def __init__(self, default_ttl_seconds: int = 3600):
        self._lock = threading.RLock()
        self._default_ttl = default_ttl_seconds
        # Key: token -> VaultEntry
        self._by_token: dict[str, VaultEntry] = {}
        # Key: (owner, session_id, raw_value) -> token
        self._lookup: dict[tuple[str, str, str], str] = {}

    def _generate_token(self, entity_type: str) -> str:
        clean_type = entity_type.lower().replace("-", "_").replace(" ", "_")
        prefix = "account" if "account" in clean_type or "bsb" in clean_type else "input"
        # 28 lowercase ascii characters to conform to existing REFERENCE regex:
        # r'^selected_[a-z_]+_[a-z]{24,40}$'
        random_suffix = "".join(secrets.choice(string.ascii_lowercase) for _ in range(28))
        return f"selected_{prefix}_{random_suffix}"

    def tokenize(
        self,
        owner: str,
        session_id: str,
        entity_type: str,
        raw_value: str,
        workflow_id: str = "default",
        ttl_seconds: int | None = None,
    ) -> str:
        if not owner or not session_id or not raw_value:
            raise ValueError("owner, session_id, and raw_value are required for tokenization")

        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        now = datetime.datetime.now(datetime.timezone.utc)
        expires_at = now + datetime.timedelta(seconds=ttl)

        with self._lock:
            # Check if this exact raw value is already tokenized in this session
            lookup_key = (owner, session_id, str(raw_value))
            existing_token = self._lookup.get(lookup_key)
            if existing_token and existing_token in self._by_token:
                entry = self._by_token[existing_token]
                if not entry.is_expired():
                    # Refresh expiry
                    entry.expires_at = max(entry.expires_at, expires_at)
                    return existing_token

            # Generate new token
            token = self._generate_token(entity_type)
            entry = VaultEntry(
                token=token,
                raw_value=str(raw_value),
                entity_type=entity_type,
                owner=owner,
                session_id=session_id,
                workflow_id=workflow_id,
                created_at=now,
                expires_at=expires_at,
            )
            self._by_token[token] = entry
            self._lookup[lookup_key] = token
            return token

    def detokenize(
        self,
        owner: str,
        session_id: str,
        token: str,
    ) -> str | None:
        if not token or not isinstance(token, str):
            return None

        with self._lock:
            entry = self._by_token.get(token)
            if not entry:
                return None

            # Enforce strict isolation
            if entry.owner != owner or entry.session_id != session_id:
                logger.warning(
                    "Token access denied: owner/session mismatch for token %s (expected %s/%s, got %s/%s)",
                    token,
                    entry.owner,
                    entry.session_id,
                    owner,
                    session_id,
                )
                return None

            if entry.is_expired():
                # Cleanup expired entry
                self._remove_entry(entry)
                return None

            return entry.raw_value

    def get_token(
        self,
        owner: str,
        session_id: str,
        raw_value: str,
    ) -> str | None:
        with self._lock:
            lookup_key = (owner, session_id, str(raw_value))
            token = self._lookup.get(lookup_key)
            if token and token in self._by_token:
                entry = self._by_token[token]
                if not entry.is_expired():
                    return token
                self._remove_entry(entry)
            return None

    def revoke_session(self, owner: str, session_id: str) -> int:
        with self._lock:
            tokens_to_remove = [
                token
                for token, entry in self._by_token.items()
                if entry.owner == owner and entry.session_id == session_id
            ]
            for token in tokens_to_remove:
                entry = self._by_token[token]
                self._remove_entry(entry)
            return len(tokens_to_remove)

    def cleanup_expired(self) -> int:
        with self._lock:
            expired = [token for token, entry in self._by_token.items() if entry.is_expired()]
            for token in expired:
                self._remove_entry(self._by_token[token])
            return len(expired)

    def _remove_entry(self, entry: VaultEntry) -> None:
        self._by_token.pop(entry.token, None)
        lookup_key = (entry.owner, entry.session_id, entry.raw_value)
        self._lookup.pop(lookup_key, None)


class RedisTokenVault(TokenVault):
    """Configurable Redis token vault provider for distributed/production deployment.

    Not initialized by default. Requires REDIS_URL and redis-py.
    """

    def __init__(self, redis_client: Any, default_ttl_seconds: int = 3600):
        self.redis = redis_client
        self.default_ttl = default_ttl_seconds

    def tokenize(
        self,
        owner: str,
        session_id: str,
        entity_type: str,
        raw_value: str,
        workflow_id: str = "default",
        ttl_seconds: int = 3600,
    ) -> str:
        raise NotImplementedError("RedisTokenVault is configured for future multi-replica deployment.")

    def detokenize(
        self,
        owner: str,
        session_id: str,
        token: str,
    ) -> str | None:
        raise NotImplementedError("RedisTokenVault is configured for future multi-replica deployment.")

    def get_token(
        self,
        owner: str,
        session_id: str,
        raw_value: str,
    ) -> str | None:
        raise NotImplementedError("RedisTokenVault is configured for future multi-replica deployment.")

    def revoke_session(self, owner: str, session_id: str) -> int:
        raise NotImplementedError("RedisTokenVault is configured for future multi-replica deployment.")

    def cleanup_expired(self) -> int:
        return 0
