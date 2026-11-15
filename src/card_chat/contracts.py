"""Versioned agent/BFF wire types; no model or transport objects cross HTTP."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=4000)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    review: str = Field(min_length=1, max_length=128)


class StructuredInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: str = Field(min_length=1, max_length=64)
    fields: dict[str, str | int | float | bool] = Field(default_factory=dict)
    selections: dict[str, str] = Field(default_factory=dict)


class SelectionOption(BaseModel):
    ref: str
    label: str


class InputField(BaseModel):
    name: str
    label: str
    type: Literal['string', 'integer', 'number', 'boolean']
    required: bool
    options: list[SelectionOption] | None = None


class InputTool(BaseModel):
    name: str
    label: str
    fields: list[InputField]


class InputOptions(BaseModel):
    tools: list[InputTool]


class Reply(BaseModel):
    message: str
    review: str | None = None
    can_confirm: bool = False
    confirmation_label: str | None = None
    id: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    input_tool: str | None = None
    data: dict | None = None


class Operation(BaseModel):
    id: str
    status_url: str
    status: Literal['pending', 'running', 'succeeded', 'failed']
    result: Reply | None = None
    error: str | None = None
