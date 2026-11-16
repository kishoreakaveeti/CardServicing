"""Grounded PolicyEvaluator adapter converting policies and facts into structured decisions."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..models import (
    PendingCustomerQuestion,
    PolicyDocument,
    PolicyEvidence,
    PolicyEvaluationResult,
)
from ..ports import PolicyEvaluator, PolicyRetriever

logger = logging.getLogger(__name__)

AFFIRMATIVE_TERMS = {
    "yes", "yes please", "yes, please", "sure", "ok", "okay", "please do",
    "yeah", "yep", "unblock it", "replace it", "order replacement", "i agree",
    "proceed", "confirm", "go ahead"
}

NEGATIVE_TERMS = {
    "no", "no thanks", "no, thanks", "no thank you", "no, thank you", "nope",
    "cancel", "don't unblock", "dont unblock", "do not unblock", "leave it",
    "keep it frozen", "keep it blocked"
}


def is_affirmative(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", text.lower().strip())
    return clean in AFFIRMATIVE_TERMS or any(term in clean for term in ["yes", "sure", "please unblock", "unblock"])


def is_negative(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", text.lower().strip())
    return clean in NEGATIVE_TERMS or any(term in clean for term in ["no", "cancel", "dont", "do not"])


class GroundedPolicyEvaluator(PolicyEvaluator):
    """Evaluates retrieved policy rules against verified domain facts and workflow state."""

    def __init__(self, retriever: PolicyRetriever):
        self._retriever = retriever

    def evaluate(
        self,
        intent: str | None = None,
        user_text: str | dict[str, Any] = "",
        facts: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        pending_question: Any | None = None,
        last_domain_event: str | None = None,
        pinned_policies: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> PolicyEvaluationResult:
        if isinstance(user_text, dict) and facts is None:
            facts = user_text
            user_text = ""
        facts = facts or {}
        state = state or {}

        # 1. Handle response to a pending interactive question first
        pending_q = pending_question or state.get("pending_customer_question")
        if pending_q:
            latest_msg = (user_text if isinstance(user_text, str) and user_text.strip() else self._get_latest_user_message(state))
            if latest_msg:
                return self._handle_pending_question_response(pending_q, latest_msg, facts, state)

        # 2. Check post-domain-event journeys (e.g. POL-003 after card_blocked_success when no explicit intent)
        if not intent:
            event = last_domain_event or state.get("last_domain_event") or facts.get("last_domain_event")
            if event == "card_blocked_success":
                return self._evaluate_post_block(facts, state)
            return PolicyEvaluationResult(permitted=True, decision_reason="No intent specified")

        # 3. Retrieve policy evidence
        evidence = self._retriever.retrieve(intent, facts)
        evidence_dicts = [e.model_dump() for e in evidence]
        pinned = {e.policy_id: e.version for e in evidence}

        # 4. Intent-specific policy evaluations
        if intent in ("issue_card", "create_card"):
            return self._evaluate_issuance(facts, state, evidence, evidence_dicts, pinned)

        if intent == "replace_card":
            return self._evaluate_replacement(facts, state, evidence, evidence_dicts, pinned)

        if intent in ("unblock_card", "block_card"):
            return self._evaluate_fincrime_action(intent, facts, state, evidence, evidence_dicts, pinned)

        return PolicyEvaluationResult(
            permitted=True,
            decision_reason=f"Action '{intent}' permitted under standard banking governance.",
            retrieved_policies=evidence_dicts,
            pinned_versions=pinned,
        )

    def validate_execution(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        facts: dict[str, Any],
        state: dict[str, Any] | None = None,
    ) -> PolicyEvaluationResult:
        """Pre-execution zero-trust validation to ensure no safety boundaries are violated."""
        # Unblocking permanently compromised card is strictly forbidden
        if tool_name == "unblock_card":
            if facts.get("is_permanently_compromised"):
                return PolicyEvaluationResult(
                    permitted=False,
                    prohibited=True,
                    decision_reason="POL-001 forbids unblocking a permanently compromised or stolen card.",
                    policy_error="ProhibitedUnblock",
                )

        # Issuing card when account has blocked card (unless updated policy specifically permits)
        if tool_name in ("issue_card", "create_card"):
            evidence = self._retriever.retrieve(tool_name, facts)
            pol1 = next((e for e in evidence if e.policy_id == "POL-001"), None)
            allow_frozen_issuance = False
            if pol1 and pol1.conditions.get("allow_issuance_on_temporary_freeze"):
                allow_frozen_issuance = True

            if facts.get("has_blocked_card") and not (facts.get("is_temporary_freeze") and allow_frozen_issuance):
                return PolicyEvaluationResult(
                    permitted=False,
                    prohibited=True,
                    decision_reason="POL-001 forbids card issuance while a blocked card exists on the account.",
                    policy_error="BlockedCardExists",
                )

        return PolicyEvaluationResult(permitted=True, decision_reason="Execution validation passed.")

    def _get_latest_user_message(self, state: dict[str, Any]) -> str:
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                return str(msg.get("content", "")).strip()
            if hasattr(msg, "content") and getattr(msg, "type", "") == "human":
                return str(msg.content).strip()
        return ""

    def _handle_pending_question_response(
        self,
        pending_q: dict[str, Any] | PendingCustomerQuestion,
        latest_msg: str,
        facts: dict[str, Any],
        state: dict[str, Any],
    ) -> PolicyEvaluationResult:
        if isinstance(pending_q, dict):
            q_type = pending_q.get("question_type")
            target_card_id = pending_q.get("target_card_id")
            target_account = pending_q.get("target_account_number")
            policy_id = pending_q.get("policy_id", "POL-001")
            policy_ver = pending_q.get("policy_version", "1.0.0")
            subsequent = pending_q.get("subsequent_intent")
        else:
            q_type = pending_q.question_type
            target_card_id = pending_q.target_card_id
            target_account = pending_q.target_account_number
            policy_id = pending_q.policy_id
            policy_ver = pending_q.policy_version
            subsequent = pending_q.subsequent_intent

        pinned = {policy_id: policy_ver}

        # 1. Affirmative response
        if is_affirmative(latest_msg):
            if q_type == "unblock_offer":
                planned = [subsequent] if subsequent else []
                return PolicyEvaluationResult(
                    permitted=True,
                    decision_reason="Customer accepted unblocking journey for frozen card under POL-001.",
                    pinned_versions=pinned,
                    recommended_delegation="fincrime_agent",
                    recommended_intent="unblock_card",
                    recommended_context={"card_id": target_card_id, "account_number": target_account},
                    customer_decision="accept",
                    required_actions=["unblock_card", "require_confirmation"],
                    planned_intents=planned,
                )
            elif q_type == "replacement_offer":
                return PolicyEvaluationResult(
                    permitted=True,
                    decision_reason="Customer accepted replacement card offer under POL-003.",
                    pinned_versions=pinned,
                    recommended_delegation="cards_agent",
                    recommended_intent="replace_card",
                    recommended_context={"card_id": target_card_id, "account_number": target_account},
                    customer_decision="accept",
                    required_actions=["replace_card", "require_confirmation"],
                )

        # 2. Negative response
        if is_negative(latest_msg):
            if q_type == "unblock_offer":
                return PolicyEvaluationResult(
                    permitted=False,
                    prohibited=True,
                    decision_reason="Customer declined unblocking. Additional card issuance remains restricted under POL-001.",
                    pinned_versions=pinned,
                    customer_decision="decline",
                    recommended_delegation="respond",
                )
            elif q_type == "replacement_offer":
                return PolicyEvaluationResult(
                    permitted=True,
                    decision_reason="Customer declined replacement card offer. Journey completed under POL-003.",
                    pinned_versions=pinned,
                    customer_decision="decline",
                    recommended_delegation="respond",
                )

        # 3. Ambiguous response
        return PolicyEvaluationResult(
            permitted=False,
            decision_reason="Customer response was ambiguous. Please confirm with 'Yes' or 'No'.",
            pinned_versions=pinned,
            pending_question=pending_q if isinstance(pending_q, PendingCustomerQuestion) else PendingCustomerQuestion(**pending_q),
            recommended_delegation="respond",
        )

    def _evaluate_post_block(self, facts: dict[str, Any], state: dict[str, Any]) -> PolicyEvaluationResult:
        evidence = self._retriever.retrieve_mandatory(policy_id="POL-003")
        pol3 = next((e for e in evidence if e.policy_id == "POL-003"), None)
        version = pol3.version if pol3 else "1.0.0"
        pinned = {"POL-003": version}

        block_reason = str(facts.get("last_block_reason", "lost")).lower()
        is_freeze = facts.get("is_temporary_freeze", block_reason in ("freeze", "frozen", "temporary_freeze"))

        offer_on_freeze = False
        if pol3 and pol3.conditions.get("offer_on_temporary_freeze"):
            offer_on_freeze = True

        should_offer = not is_freeze or offer_on_freeze

        if should_offer:
            last4 = facts.get("last_blocked_card_last4", "your card")
            card_id = facts.get("blocked_card_id")
            account = facts.get("account_number")
            q = PendingCustomerQuestion(
                question_type="replacement_offer",
                question_text=f"Your card ending in {last4} has been safely blocked. Would you like to order a replacement card?",
                target_card_id=card_id,
                target_account_number=account,
                policy_id="POL-003",
                policy_version=version,
                yes_intent="replace_card",
                no_intent="complete",
            )
            return PolicyEvaluationResult(
                permitted=True,
                decision_reason="POL-003 mandates proactively offering a replacement card after card blocking.",
                pinned_versions=pinned,
                pending_question=q,
                recommended_delegation="respond",
            )

        return PolicyEvaluationResult(
            permitted=True,
            decision_reason="Card safely blocked. Under POL-003, no replacement is proactively offered for temporary freeze.",
            pinned_versions=pinned,
        )

    def _evaluate_issuance(
        self,
        facts: dict[str, Any],
        state: dict[str, Any],
        evidence: list[PolicyEvidence],
        evidence_dicts: list[dict[str, Any]],
        pinned: dict[str, str],
    ) -> PolicyEvaluationResult:
        has_blocked = facts.get("has_blocked_card", False)
        if not has_blocked:
            return PolicyEvaluationResult(
                permitted=True,
                decision_reason="POL-001 check passed: No blocked cards exist on this account.",
                retrieved_policies=evidence_dicts,
                pinned_versions=pinned,
                recommended_delegation="cards_agent",
                recommended_intent="issue_card",
            )

        # Blocked card exists
        pol1 = next((e for e in evidence if e.policy_id == "POL-001"), None)
        pol1_version = pol1.version if pol1 else "1.0.0"

        # Check if policy was updated (e.g. Scenario C: allow_issuance_on_temporary_freeze)
        if pol1 and pol1.conditions.get("allow_issuance_on_temporary_freeze") and facts.get("is_temporary_freeze"):
            return PolicyEvaluationResult(
                permitted=True,
                decision_reason=f"Under updated policy POL-001 (v{pol1_version}), additional card issuance is permitted while card is temporarily frozen, subject to standard eligibility checks.",
                retrieved_policies=evidence_dicts,
                pinned_versions=pinned,
                recommended_delegation="cards_agent",
                recommended_intent="issue_card",
            )

        is_freeze = facts.get("is_temporary_freeze", False)
        is_compromised = facts.get("is_permanently_compromised", False)

        if is_compromised:
            return PolicyEvaluationResult(
                permitted=False,
                prohibited=True,
                decision_reason="POL-001 strictly prohibits issuing a new card or unblocking when a card is permanently lost, stolen, or compromised. Please request a replacement card under POL-002 instead.",
                retrieved_policies=evidence_dicts,
                pinned_versions=pinned,
                restrictions_enforced=["prohibit_issuance_if_any_card_blocked", "never_suggest_unblocking_permanently_compromised"],
                recommended_delegation="respond",
            )

        if is_freeze:
            last4 = facts.get("blocked_card_last4") or (facts.get("blocked_cards", [{}])[0].get("card_number", "card")[-4:] if facts.get("blocked_cards") else "your card")
            card_id = facts.get("blocked_card_id") or (facts.get("blocked_cards", [{}])[0].get("id") or facts.get("blocked_cards", [{}])[0].get("card_id"))
            account = facts.get("account_number") or (facts.get("blocked_cards", [{}])[0].get("account_number") if facts.get("blocked_cards") else None)
            q = PendingCustomerQuestion(
                question_type="unblock_offer",
                question_text=f"Your card ending in {last4} is temporarily frozen. Under banking policy POL-001, would you like to unblock it first before issuing a new card?",
                target_card_id=card_id,
                target_account_number=account,
                policy_id="POL-001",
                policy_version=pol1_version,
                yes_intent="unblock_card",
                no_intent="cancel",
                subsequent_intent="issue_card",
            )
            return PolicyEvaluationResult(
                permitted=False,
                decision_reason="POL-001 requires offering an eligible unblocking journey before additional card issuance.",
                retrieved_policies=evidence_dicts,
                pinned_versions=pinned,
                restrictions_enforced=["prohibit_issuance_if_any_card_blocked"],
                pending_question=q,
                recommended_delegation="respond",
            )

        return PolicyEvaluationResult(
            permitted=False,
            prohibited=True,
            decision_reason="POL-001 restricts card issuance while an existing card is blocked.",
            retrieved_policies=evidence_dicts,
            pinned_versions=pinned,
            restrictions_enforced=["prohibit_issuance_if_any_card_blocked"],
            recommended_delegation="respond",
        )

    def _evaluate_replacement(
        self,
        facts: dict[str, Any],
        state: dict[str, Any],
        evidence: list[PolicyEvidence],
        evidence_dicts: list[dict[str, Any]],
        pinned: dict[str, str],
    ) -> PolicyEvaluationResult:
        return PolicyEvaluationResult(
            permitted=True,
            decision_reason="POL-002 authorizes replacement of a compromised or blocked card without unblocking the original card.",
            retrieved_policies=evidence_dicts,
            pinned_versions=pinned,
            recommended_delegation="cards_agent",
            recommended_intent="replace_card",
        )

    def _evaluate_fincrime_action(
        self,
        intent: str,
        facts: dict[str, Any],
        state: dict[str, Any],
        evidence: list[PolicyEvidence],
        evidence_dicts: list[dict[str, Any]],
        pinned: dict[str, str],
    ) -> PolicyEvaluationResult:
        if intent == "unblock_card" and facts.get("is_permanently_compromised"):
            return PolicyEvaluationResult(
                permitted=False,
                prohibited=True,
                decision_reason="POL-001 forbids unblocking a permanently lost, stolen, or compromised card.",
                retrieved_policies=evidence_dicts,
                pinned_versions=pinned,
                restrictions_enforced=["never_suggest_unblocking_permanently_compromised"],
                recommended_delegation="respond",
            )

        return PolicyEvaluationResult(
            permitted=True,
            decision_reason=f"Action '{intent}' permitted under FinCrime security policies.",
            retrieved_policies=evidence_dicts,
            pinned_versions=pinned,
            recommended_delegation="fincrime_agent",
            recommended_intent=intent,
        )
