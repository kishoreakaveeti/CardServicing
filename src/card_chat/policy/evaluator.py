"""Policy rule evaluator integrating verified facts, retrieved RAG policies, and workflow state."""
from __future__ import annotations

import logging
import re
from typing import Any

from card_chat.policy.models import (
    PendingCustomerQuestion,
    PolicyChunk,
    PolicyEvaluationResult,
)
from card_chat.policy.store import PolicyStore

logger = logging.getLogger(__name__)

AFFIRMATIVE_TERMS = {
    "yes", "yes please", "sure", "ok", "okay", "yep", "yeah",
    "unblock", "unblock it", "replace", "replace it", "order",
    "order replacement", "proceed", "please", "please do",
    "confirm", "go ahead", "accept",
}

NEGATIVE_TERMS = {
    "no", "no thanks", "no thank you", "nope", "cancel", "don't",
    "dont", "do not", "stop", "decline", "neither", "leave it",
    "keep blocked", "not now",
}


def is_affirmative(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", text.strip().lower())
    if clean in AFFIRMATIVE_TERMS:
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", clean) for term in AFFIRMATIVE_TERMS)


def is_negative(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", text.strip().lower())
    if clean in NEGATIVE_TERMS:
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", clean) for term in NEGATIVE_TERMS)


class PolicyEvaluator:
    """Evaluates retrieved company policies against verified domain facts and workflow state."""

    def __init__(self, store: PolicyStore):
        self.store = store

    def evaluate(
        self,
        intent: str | None,
        user_text: str,
        facts: dict[str, Any],
        last_domain_event: str | None = None,
        pending_question: dict[str, Any] | PendingCustomerQuestion | None = None,
        pinned_policies: dict[str, str] | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluate applicable policies and return structured decision."""
        pinned = dict(pinned_policies or {})
        retrieved_refs: list[dict[str, Any]] = []

        # Convert dict pending question to model if needed
        if isinstance(pending_question, dict):
            try:
                pending_question = PendingCustomerQuestion(**pending_question)
            except Exception:
                pending_question = None

        # 1. Handle user response to an active policy-driven pending question
        if pending_question and user_text.strip():
            return self._handle_pending_question_response(
                pending_question, user_text, facts, pinned
            )

        # 2. Handle Post-Execution Domain Events (e.g. card_blocked_success)
        if last_domain_event == "card_blocked_success":
            return self._evaluate_post_block(facts, pinned)

        # 3. Intent-Specific Policy Evaluations
        if intent in ("issue_card", "create_card"):
            return self._evaluate_issuance(intent, user_text, facts, pinned)

        if intent == "replace_card":
            return self._evaluate_replacement(intent, user_text, facts, pinned)

        if intent in ("block_card", "unblock_card"):
            return self._evaluate_fincrime_action(intent, user_text, facts, pinned)

        # 4. Fallback / Read-only / Other Intents
        return PolicyEvaluationResult(
            permitted=True,
            prohibited=False,
            decision_reason="Standard service intent without custom policy restrictions.",
            pinned_versions=pinned,
        )

    def _handle_pending_question_response(
        self,
        pending: PendingCustomerQuestion,
        user_text: str,
        facts: dict[str, Any],
        pinned: dict[str, str],
    ) -> PolicyEvaluationResult:
        """Interpret customer response in the context of the pending question."""
        user_lower = user_text.strip().lower()
        accepted = is_affirmative(user_lower)
        declined = is_negative(user_lower)

        # Pin the policy that originated this question
        pinned[pending.policy_id] = pending.policy_version

        # Case A: Customer was offered an unblocking journey (POL-001)
        if pending.question_type == "unblock_offer":
            if accepted:
                return PolicyEvaluationResult(
                    permitted=True,
                    prohibited=False,
                    decision_reason="Customer accepted unblocking journey for frozen card under POL-001.",
                    pinned_versions=pinned,
                    recommended_delegation="fincrime_agent",
                    recommended_intent="unblock_card",
                    recommended_context={
                        "card_id": pending.target_card_id,
                        "account_number": pending.target_account_number,
                        "subsequent_intent": pending.subsequent_intent,
                    },
                    required_actions=["unblock_card", "require_customer_confirmation"],
                )
            elif declined:
                return PolicyEvaluationResult(
                    permitted=False,
                    prohibited=True,
                    decision_reason=(
                        "Under banking policy POL-001, additional cards cannot be issued while an account "
                        "has a blocked card. Since you declined to unblock the card, card issuance cannot proceed."
                    ),
                    pinned_versions=pinned,
                    recommended_delegation="respond",
                    restrictions_enforced=["prohibit_issuance_if_any_card_blocked"],
                )

        # Case B: Customer was offered a replacement card (POL-003)
        if pending.question_type == "replacement_offer":
            if accepted:
                # Retrieve POL-002 for replacement eligibility
                pol002_chunks = self.store.retrieve_mandatory(policy_id="POL-002")
                pol002_version = pol002_chunks[0].version if pol002_chunks else "1.0.0"
                pinned["POL-002"] = pol002_version

                return PolicyEvaluationResult(
                    permitted=True,
                    prohibited=False,
                    decision_reason="Customer accepted replacement card offer under POL-003/POL-002.",
                    pinned_versions=pinned,
                    recommended_delegation="cards_agent",
                    recommended_intent="replace_card",
                    recommended_context={
                        "card_id": pending.target_card_id,
                        "reason": "lost card replacement",
                    },
                    required_actions=["replace_card", "require_customer_confirmation"],
                )
            elif declined:
                return PolicyEvaluationResult(
                    permitted=True,
                    prohibited=False,
                    decision_reason="Customer declined card replacement. Workflow concluded safely without replacement.",
                    pinned_versions=pinned,
                    recommended_delegation="respond",
                )

        # Clarification needed if neither clearly affirmative nor negative
        return PolicyEvaluationResult(
            permitted=True,
            prohibited=False,
            decision_reason="Awaiting clear customer confirmation (Yes/No).",
            pinned_versions=pinned,
            pending_question=pending,
            recommended_delegation="respond",
        )

    def _evaluate_post_block(
        self,
        facts: dict[str, Any],
        pinned: dict[str, str],
    ) -> PolicyEvaluationResult:
        """Evaluate POL-003 after successful card block."""
        chunks = self.store.retrieve(
            query="Post-block customer journey replacement offer",
            intent="block_card",
            event_type="card_blocked_success",
        )

        doc = self.store.get_document("POL-003")
        if not doc:
            # Fail closed if mandatory policy missing
            return PolicyEvaluationResult(
                permitted=False,
                prohibited=True,
                policy_error="Mandatory policy POL-003 not found in repository. Failing closed.",
                pinned_versions=pinned,
            )

        pinned["POL-003"] = doc.version
        retrieved_refs = [
            {"policy_id": doc.policy_id, "version": doc.version, "title": doc.title, "source": doc.source}
        ]

        # Extract blocked card facts
        blocked_card_id = facts.get("last_blocked_card_id") or facts.get("card_id")
        block_reason = (facts.get("last_block_reason") or facts.get("reason") or "").lower()
        last4 = facts.get("last_blocked_card_last4", "your card")

        cond = doc.conditions
        compromised_reasons = cond.get("block_reasons", ["lost", "stolen", "compromised", "fraud", "destroyed"])
        offer_on_freeze = bool(cond.get("offer_on_temporary_freeze", False))

        is_compromised = any(term in block_reason for term in compromised_reasons)
        is_freeze = any(term in block_reason for term in ("freeze", "frozen", "temporary"))

        should_offer_replacement = is_compromised or (is_freeze and offer_on_freeze)

        if should_offer_replacement:
            question_text = (
                f"Your card ending in {last4} has been safely blocked. "
                f"Under company policy POL-003 (v{doc.version}), would you like to request a replacement card?"
            )
            pending = PendingCustomerQuestion(
                question_type="replacement_offer",
                question_text=question_text,
                target_card_id=blocked_card_id,
                policy_id="POL-003",
                policy_version=doc.version,
                yes_intent="replace_card",
            )
            return PolicyEvaluationResult(
                permitted=True,
                prohibited=False,
                decision_reason=f"POL-003 v{doc.version} mandates offering replacement card after blocking.",
                retrieved_policies=retrieved_refs,
                pinned_versions=pinned,
                pending_question=pending,
                recommended_delegation="respond",
                required_actions=["offer_replacement_card", "await_customer_decision"],
            )

        # If temporary freeze and policy does NOT offer replacement: complete workflow
        return PolicyEvaluationResult(
            permitted=True,
            prohibited=False,
            decision_reason=f"POL-003 v{doc.version}: Card frozen temporarily. Replacement not proactively offered.",
            retrieved_policies=retrieved_refs,
            pinned_versions=pinned,
            recommended_delegation="respond",
        )

    def _evaluate_issuance(
        self,
        intent: str,
        user_text: str,
        facts: dict[str, Any],
        pinned: dict[str, str],
    ) -> PolicyEvaluationResult:
        """Evaluate POL-001 and POL-004 when customer requests new/additional card."""
        # 1. Deterministic mandatory lookup of POL-001 & POL-004
        doc_pol001 = self.store.get_document("POL-001")
        doc_pol004 = self.store.get_document("POL-004")

        if not doc_pol001:
            return PolicyEvaluationResult(
                permitted=False,
                prohibited=True,
                policy_error="Mandatory policy POL-001 missing. Failing closed.",
                pinned_versions=pinned,
            )

        pinned["POL-001"] = doc_pol001.version
        if doc_pol004:
            pinned["POL-004"] = doc_pol004.version

        retrieved_refs = [
            {"policy_id": doc_pol001.policy_id, "version": doc_pol001.version, "title": doc_pol001.title, "source": doc_pol001.source}
        ]

        has_blocked = bool(facts.get("has_blocked_card"))
        blocked_cards = facts.get("blocked_cards", [])

        # If account has NO blocked card, issuance is permitted
        if not has_blocked and not blocked_cards:
            return PolicyEvaluationResult(
                permitted=True,
                prohibited=False,
                decision_reason="Account is in good standing with no active block restrictions.",
                retrieved_policies=retrieved_refs,
                pinned_versions=pinned,
                recommended_delegation="cards_agent",
                recommended_intent=intent,
                required_actions=["verify_caller_authorization", "require_customer_confirmation"],
            )

        # Account has a blocked card -> POL-001 restrictions apply!
        blocked_card = blocked_cards[0] if blocked_cards else {}
        card_id = blocked_card.get("id") or blocked_card.get("card_id")
        reason = (blocked_card.get("reason") or "").lower()
        account_number = blocked_card.get("account_number") or facts.get("account_number")

        is_compromised = any(term in reason for term in ("lost", "stolen", "fraud", "compromised", "destroyed"))
        is_temporary_freeze = facts.get("temporary_freeze", False) or not is_compromised

        # Check if updated policy permits issuance on temporary freeze (Scenario C)
        conditions = doc_pol001.conditions or {}
        allow_freeze_issuance = bool(conditions.get("allow_issuance_on_temporary_freeze"))

        if is_temporary_freeze and allow_freeze_issuance:
            return PolicyEvaluationResult(
                permitted=True,
                prohibited=False,
                decision_reason=(
                    f"Under updated policy POL-001 (v{doc_pol001.version}), additional card issuance is "
                    "permitted while an existing card is temporarily frozen, subject to standard eligibility checks."
                ),
                retrieved_policies=retrieved_refs,
                pinned_versions=pinned,
                recommended_delegation="cards_agent",
                recommended_intent=intent,
                recommended_context={"account_number": account_number},
                required_actions=["verify_caller_authorization", "require_customer_confirmation"],
            )

        if is_temporary_freeze:
            # Eligible for unblocking journey under POL-001
            question_text = (
                "Cannot issue a new card for this account because it has an active blocked card. "
                f"Under banking policy POL-001 (v{doc_pol001.version}), the blocked card must be unblocked "
                "or resolved before an additional card can be issued. "
                "Since this card is temporarily frozen, would you like to unblock it first?"
            )
            pending = PendingCustomerQuestion(
                question_type="unblock_offer",
                question_text=question_text,
                target_card_id=card_id,
                target_account_number=account_number,
                policy_id="POL-001",
                policy_version=doc_pol001.version,
                yes_intent="unblock_card",
                subsequent_intent=intent,
            )
            return PolicyEvaluationResult(
                permitted=False,
                prohibited=True,
                decision_reason=(
                    f"Under POL-001 v{doc_pol001.version}, issuing an additional card is prohibited while "
                    "a blocked card exists. Card is eligible for unblocking journey."
                ),
                retrieved_policies=retrieved_refs,
                pinned_versions=pinned,
                pending_question=pending,
                recommended_delegation="respond",
                restrictions_enforced=["prohibit_issuance_if_any_card_blocked"],
                required_actions=["offer_unblocking_journey_if_eligible"],
            )

        # Permanently compromised card -> NEVER suggest unblocking (POL-001 section 3.2 & POL-002)
        doc_pol002 = self.store.get_document("POL-002")
        if doc_pol002:
            pinned["POL-002"] = doc_pol002.version
            retrieved_refs.append(
                {"policy_id": doc_pol002.policy_id, "version": doc_pol002.version, "title": doc_pol002.title, "source": doc_pol002.source}
            )

        question_text = (
            "Cannot issue a new additional card for this account because it has an existing card permanently "
            f"blocked due to loss or compromise. Under banking security rules (POL-001 / POL-002), compromised "
            "cards can never be unblocked and additional cards cannot be issued. "
            "However, you may request a replacement card instead. Would you like to request a replacement card?"
        )
        pending = PendingCustomerQuestion(
            question_type="replacement_offer",
            question_text=question_text,
            target_card_id=card_id,
            target_account_number=account_number,
            policy_id="POL-002",
            policy_version=doc_pol002.version if doc_pol002 else "1.0.0",
            yes_intent="replace_card",
        )
        return PolicyEvaluationResult(
            permitted=False,
            prohibited=True,
            decision_reason=(
                "Under POL-001 and POL-002, permanently compromised cards cannot be unblocked and additional "
                "issuance is prohibited. Offered card replacement journey instead."
            ),
            retrieved_policies=retrieved_refs,
            pinned_versions=pinned,
            pending_question=pending,
            recommended_delegation="respond",
            restrictions_enforced=[
                "prohibit_issuance_if_any_card_blocked",
                "never_suggest_unblocking_permanently_compromised",
            ],
            required_actions=["evaluate_replacement_eligibility"],
        )

    def _evaluate_replacement(
        self,
        intent: str,
        user_text: str,
        facts: dict[str, Any],
        pinned: dict[str, str],
    ) -> PolicyEvaluationResult:
        """Evaluate POL-002 when replacing a card."""
        doc_pol002 = self.store.get_document("POL-002")
        if not doc_pol002:
            return PolicyEvaluationResult(
                permitted=False,
                prohibited=True,
                policy_error="Mandatory policy POL-002 missing. Failing closed.",
                pinned_versions=pinned,
            )

        pinned["POL-002"] = doc_pol002.version
        retrieved_refs = [
            {"policy_id": doc_pol002.policy_id, "version": doc_pol002.version, "title": doc_pol002.title, "source": doc_pol002.source}
        ]

        target_card_id = facts.get("card_id") or facts.get("target_card_id")
        card_status = facts.get("card_status")

        # If card is active (not blocked), replacement is ineligible
        if card_status and card_status != "blocked":
            return PolicyEvaluationResult(
                permitted=False,
                prohibited=True,
                decision_reason="Under POL-002, only cards in a blocked status are eligible for replacement.",
                retrieved_policies=retrieved_refs,
                pinned_versions=pinned,
                recommended_delegation="respond",
                restrictions_enforced=["require_blocked_status_for_replacement"],
            )

        return PolicyEvaluationResult(
            permitted=True,
            prohibited=False,
            decision_reason=f"Card is eligible for replacement under POL-002 v{doc_pol002.version}.",
            retrieved_policies=retrieved_refs,
            pinned_versions=pinned,
            recommended_delegation="cards_agent",
            recommended_intent="replace_card",
            required_actions=["verify_card_is_permanently_blocked", "prepare_replacement_proposal", "require_independent_confirmation"],
            restrictions_enforced=["distinguish_from_additional_issuance"],
        )

    def _evaluate_fincrime_action(
        self,
        intent: str,
        user_text: str,
        facts: dict[str, Any],
        pinned: dict[str, str],
    ) -> PolicyEvaluationResult:
        """Evaluate blocking/unblocking under POL-004 and risk policies."""
        doc_pol004 = self.store.get_document("POL-004")
        if doc_pol004:
            pinned["POL-004"] = doc_pol004.version

        return PolicyEvaluationResult(
            permitted=True,
            prohibited=False,
            decision_reason=f"Action {intent} delegated to FinCrime subject to deterministic confirmation.",
            pinned_versions=pinned,
            recommended_delegation="fincrime_agent",
            recommended_intent=intent,
            required_actions=["require_deterministic_customer_confirmation", "generate_and_enforce_idempotency_key"],
        )
