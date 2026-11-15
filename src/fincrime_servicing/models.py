"""Domain models for FinCrime risk and card blocking."""
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field


class CardBlockRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(default="Customer requested", min_length=1, max_length=200, title="Reason")


class CardBlockResult(BaseModel):
    card_id: str = Field(title="Card Id")
    status: str = Field(default="blocked", title="Status")
    reason: str = Field(default="Customer requested", title="Reason")
    account_number: str = Field(default="", title="Account Number")
    synced: bool = Field(default=True, title="Synchronized With Cards")
    message: str = Field(default="Card successfully blocked", title="Message")


class AccountStatusResult(BaseModel):
    account_number: str
    has_blocked_cards: bool
    blocked_card_ids: list[str] = Field(default_factory=list)


class AuditRecord(BaseModel):
    id: str
    event: str
    customer_id: str
    card_id: str
    account_number: str = ""
    details: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
