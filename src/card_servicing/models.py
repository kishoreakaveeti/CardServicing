from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator

class ItemCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""

class Item(ItemCreate):
    id: int

class CardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_name: str = Field(min_length=1, max_length=100, examples=["alex.smith"])
    given_name: str = Field(min_length=1, max_length=100, examples=["Alex"])
    account_number: str = Field(min_length=1, max_length=64, examples=["0012345678"])

    @field_validator('user_name', 'given_name', 'account_number')
    @classmethod
    def reject_controls(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('control characters are not allowed')
        return value

class Card(CardCreate):
    id: UUID
    card_number: str = Field(pattern=r'^SIM-[0-9a-f]{32}$', description='Simulation identifier; not a payment network PAN.')
    status: Literal['created'] = 'created'
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
