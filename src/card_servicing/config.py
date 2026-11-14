from dataclasses import dataclass

@dataclass(frozen=True)
class CardSettings:
    """Extension point for future card origination policy."""
    pass
