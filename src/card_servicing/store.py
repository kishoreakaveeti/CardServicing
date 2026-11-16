from uuid import UUID, uuid4
from threading import RLock

from card_servicing.idempotency import IdempotencyConflict, fingerprint

from card_servicing.models import Item, ItemCreate, Card, CardCreate, CardIssuance


class ItemStore:
    def __init__(self) -> None:
        self._items: dict[int, Item] = {}
        self._next_id = 1

    def list(self) -> list[Item]:
        return list(self._items.values())

    def get(self, item_id: int) -> Item | None:
        return self._items.get(item_id)

    def create(self, item: ItemCreate) -> Item:
        created = Item(id=self._next_id, **item.model_dump())
        self._items[created.id] = created
        self._next_id += 1
        return created

    def update(self, item_id: int, item: ItemCreate) -> Item | None:
        if item_id not in self._items:
            return None
        updated = Item(id=item_id, **item.model_dump())
        self._items[item_id] = updated
        return updated

    def delete(self, item_id: int) -> bool:
        return self._items.pop(item_id, None) is not None


class CardStore:
    """Isolated memory adapter for simulated card origination."""

    dependency_name = 'card_store'

    def initialize(self) -> None:
        pass

    def __init__(self) -> None:
        self._cards: dict[UUID, Card] = {}
        self._idempotency: dict[str, tuple[str, Card]] = {}
        self._lock = RLock()
        self._issuances = {}

    def check_readiness(self) -> bool:
        # Test/development memory storage has no external dependency to probe.
        return True

    def create_idempotent(self, instruction: CardCreate, key: str) -> tuple[Card, bool]:
        with self._lock:
            identity = fingerprint(instruction)
            if key in self._idempotency:
                previous, card = self._idempotency[key]
                if previous != identity:
                    raise IdempotencyConflict()
                return card, True
            card = self.create(instruction)
            self._idempotency[key] = identity, card
            return card, False

    def create(self, instruction: CardCreate) -> Card:
        card = Card(id=uuid4(), card_number="SIM-" + uuid4().hex, **instruction.model_dump(mode="json"))
        self._cards[card.id] = card
        return card

    def issue_idempotent(self, instruction, key, *, additional=False, customer_id=None):
        from card_servicing.ports import AccountNotOwned
        if customer_id is not None:
            raise AccountNotOwned()  # Memory adapter does not contain managed accounts.
        identity = fingerprint(instruction) + (':additional' if additional else ':initial')
        with self._lock:
            if key in self._issuances:
                previous, result = self._issuances[key]
                if previous != identity:
                    raise IdempotencyConflict()
                return result, True
            if key in self._idempotency:
                if additional:
                    raise IdempotencyConflict()
                card, _ = self.create_idempotent(instruction, key)
                result, replayed = CardIssuance.for_card(card, 'created'), True
            else:
                existing = next((card for card in self._cards.values() if card.account_number == instruction.account_number
                                 and card.user_name == instruction.user_name), None)
                if existing and not additional:
                    result = CardIssuance.for_card(existing, 'already_exists')
                else:
                    card, _ = self.create_idempotent(instruction, key)
                    result = CardIssuance.for_card(card, 'additional_created' if additional else 'created')
                replayed = False
            self._issuances[key] = identity, result
            return result, replayed

    def replace_idempotent(self, card_id, key, *, customer_id=None):
        from card_servicing.ports import AccountNotOwned
        identity = f"replace:{card_id}"
        with self._lock:
            target_card = self.get_card(card_id)
            if not target_card:
                raise ValueError("Card not found")
            if target_card.status != "blocked":
                raise ValueError("Only blocked cards can be replaced")
            if key in self._issuances:
                previous, result = self._issuances[key]
                if previous != identity:
                    raise IdempotencyConflict()
                return result, True
            instruction = CardCreate(
                user_name=target_card.user_name,
                given_name=target_card.given_name,
                account_number=target_card.account_number,
            )
            card = self.create(instruction)
            result = CardIssuance.for_card(card, 'replacement_created')
            self._issuances[key] = identity, result
            return result, False

    def get_card(self, card_id: str):
        with self._lock:
            for c in self._cards.values():
                if str(c.id) == str(card_id) or c.card_number == str(card_id):
                    return c
            return None

    def update_card_status(self, card_id: str, status: str) -> bool:
        with self._lock:
            for c in self._cards.values():
                if str(c.id) == str(card_id) or c.card_number == str(card_id):
                    c.status = status
                    return True
            return False

    def has_blocked_cards_for_account(self, account_number: str) -> bool:
        with self._lock:
            return any(c.account_number == account_number and c.status == 'blocked' for c in self._cards.values())

    def list(self) -> list[Card]:
        return list(self._cards.values())
