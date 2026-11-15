"""Infrastructure contracts consumed by application/HTTP code."""
from typing import Protocol

from card_servicing.models import Card, CardCreate, CardIssuance


class StorageUnavailable(Exception):
    """Expected storage failure; adapter details must not reach HTTP responses."""


class AccountNotOwned(Exception):
    """The authenticated customer does not own the selected managed account."""


class BlockedCardExists(Exception):
    """Cannot issue a new card while an existing card on the account is blocked."""


class CardStorePort(Protocol):
    dependency_name: str

    def initialize(self) -> None: ...
    def check_readiness(self) -> bool: ...
    def create_idempotent(self, instruction: CardCreate, key: str) -> tuple[Card, bool]: ...
    def issue_idempotent(self, instruction: CardCreate, key: str, *, additional=False,
                        customer_id=None) -> tuple[CardIssuance, bool]: ...


class TelemetryPort(Protocol):
    def start(self) -> None: ...
    def account_fields(self, instruction: CardCreate) -> dict: ...
    def emit(self, kind: str, event: str, **fields) -> None: ...
    def close(self) -> None: ...
