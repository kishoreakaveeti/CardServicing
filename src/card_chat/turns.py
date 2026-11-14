"""Provider-neutral model outcomes. A tool call is a proposal, never execution."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CustomerReply:
    text: str


@dataclass(frozen=True)
class ProposedToolCall:
    name: str
    arguments: dict
