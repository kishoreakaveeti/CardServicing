"""Business domain for FinCrime service: authorization, card blocking, and Cards DB sync."""
import logging
from fastapi import HTTPException
import httpx

from fincrime_servicing.models import CardBlockResult, AccountStatusResult
from fincrime_servicing.persistence import SQLiteFinCrimeStore

logger = logging.getLogger(__name__)


class FinCrimeService:
    def __init__(self, store: SQLiteFinCrimeStore, cards_api_url: str | None = None,
                 cards_service=None, service_headers_fn=None):
        self.store = store
        self.cards_api_url = (cards_api_url or "").rstrip("/")
        self.cards_service = cards_service
        self.service_headers_fn = service_headers_fn

    async def block_card(self, customer: dict, card_id: str, reason: str,
                         key: str | None = None, account_number: str = "") -> tuple[CardBlockResult, bool]:
        customer_id = customer.get("sub", "")
        if not customer_id:
            raise HTTPException(401, "Sign in required to block cards")

        # Resolve card details / verify ownership if cards_service is directly attached
        if self.cards_service:
            card = self.cards_service.store.get_card(card_id)
            if not card:
                raise HTTPException(404, "Card not found")
            if customer and not self.cards_service.owns(customer_id, card.account_number):
                raise HTTPException(403, "Card does not belong to authorized customer")
            account_number = card.account_number

        # 1. Record blocking in FinCrime database with idempotency
        result, replayed = self.store.block_card_idempotent(
            customer_id=customer_id,
            card_id=card_id,
            account_number=account_number,
            reason=reason,
            request_key=key,
            synced=True
        )

        # 2. Synchronize card status to Cards DB
        synced = await self._sync_to_cards_api(card_id, customer, reason)
        if not synced and not result.synced:
            self.store.mark_card_synced(card_id, False)
            result.synced = False

        return result, replayed

    async def unblock_card(self, customer: dict, card_id: str, reason: str,
                           key: str | None = None, account_number: str = "") -> tuple[CardBlockResult, bool]:
        customer_id = customer.get("sub", "")
        if not customer_id:
            raise HTTPException(401, "Sign in required to unblock cards")

        if self.cards_service:
            card = self.cards_service.store.get_card(card_id)
            if not card:
                raise HTTPException(404, "Card not found")
            if customer and not self.cards_service.owns(customer_id, card.account_number):
                raise HTTPException(403, "Card does not belong to authorized customer")
            if card.status != "blocked":
                raise HTTPException(400, "Only blocked cards can be unblocked")
            account_number = card.account_number

        # Security Policy: Permanently blocked or compromised cards can NEVER be unblocked
        existing_block = self.store.get_blocked_card(card_id)
        if existing_block and any(term in (existing_block.reason or "").lower() for term in ("lost", "stolen", "fraud", "compromised", "destroyed")):
            raise HTTPException(400, "Permanently blocked or compromised cards cannot be unblocked for security reasons. Please order a replacement card.")

        result, replayed = self.store.unblock_card_idempotent(
            customer_id=customer_id,
            card_id=card_id,
            account_number=account_number,
            reason=reason,
            request_key=key,
            synced=True
        )

        synced = await self._sync_unblock_to_cards_api(card_id, customer, reason)
        if not synced and not result.synced:
            self.store.mark_card_synced(card_id, False)
            result.synced = False

        return result, replayed

    async def _sync_unblock_to_cards_api(self, card_id: str, customer: dict, reason: str) -> bool:
        """Synchronize card unblock status to the authoritative Cards database."""
        if self.cards_service:
            try:
                self.cards_service.unblock_card(customer, card_id, reason)
                return True
            except Exception as exc:
                logger.error("Failed to sync card unblock to in-process Cards service: %s", exc)
                return False

        if self.cards_api_url:
            headers = {}
            if self.service_headers_fn:
                try:
                    headers = await self.service_headers_fn()
                except Exception:
                    pass
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{self.cards_api_url}/banking/cards/{card_id}/unblock",
                        json={"reason": reason},
                        headers=headers
                    )
                    return resp.is_success
            except Exception as exc:
                logger.error("Failed to sync card unblock to Cards API over HTTP: %s", exc)
                return False

        return True

    async def _sync_to_cards_api(self, card_id: str, customer: dict, reason: str) -> bool:
        """Synchronize card status to the authoritative Cards database."""
        # A) In-process service call
        if self.cards_service:
            try:
                self.cards_service.block_card(customer, card_id, reason)
                return True
            except Exception as exc:
                logger.error("Failed to sync card block to in-process Cards service: %s", exc)
                return False

        # B) HTTP API call to Cards service
        if self.cards_api_url:
            headers = {}
            if self.service_headers_fn:
                try:
                    headers = await self.service_headers_fn()
                except Exception:
                    pass
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{self.cards_api_url}/banking/cards/{card_id}/block",
                        json={"reason": reason},
                        headers=headers
                    )
                    return resp.is_success
            except Exception as exc:
                logger.error("Failed to sync card block to Cards API over HTTP: %s", exc)
                return False

        return True

    def is_account_blocked(self, account_number: str) -> bool:
        return self.store.is_account_blocked(account_number)

    def has_temporary_freeze(self, account_number: str) -> bool:
        """Check if an account has cards blocked due to a temporary customer freeze (can be unblocked)."""
        blocked = self.store.get_blocked_cards_for_account(account_number)
        return any(
            not any(term in (b.reason or "").lower() for term in ("lost", "stolen", "fraud", "compromised", "destroyed"))
            for b in blocked
        )

    def has_compromised_cards(self, account_number: str) -> bool:
        """Check if an account has cards permanently blocked due to compromise/loss/theft."""
        blocked = self.store.get_blocked_cards_for_account(account_number)
        return any(
            any(term in (b.reason or "").lower() for term in ("lost", "stolen", "fraud", "compromised", "destroyed"))
            for b in blocked
        )

    def get_account_status(self, account_number: str) -> AccountStatusResult:
        blocked = self.store.get_blocked_cards_for_account(account_number)
        return AccountStatusResult(
            account_number=account_number,
            has_blocked_cards=bool(blocked),
            blocked_card_ids=[b.card_id for b in blocked]
        )

    def get_blocked_card(self, card_id: str) -> CardBlockResult | None:
        return self.store.get_blocked_card(card_id)
