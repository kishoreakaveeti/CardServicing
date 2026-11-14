"""Canonical identity of validated, normalized origination fields."""
import hashlib
import json

from card_servicing.models import CardCreate


class IdempotencyConflict(Exception):
    """A key was already committed for a different instruction."""


def fingerprint(instruction: CardCreate) -> str:
    payload = instruction.model_dump(mode='json')
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
