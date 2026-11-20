"""Grounded PolicyEvaluator adapter converting policies and facts into structured decisions."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..models import (
    PendingCustomerQuestion,
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
    return clean in AFFIRMATIVE_TERMS or any(re.search(rf"\b{term}\b", clean) for term in ("yes", "sure", "please unblock", "unblock"))


def is_negative(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", text.lower().strip())
    return clean in NEGATIVE_TERMS or any(re.search(rf"\b{term}\b", clean) for term in ("no", "cancel", "dont", "do not"))


class GroundedPolicyEvaluator(PolicyEvaluator):
    """Evaluates retrieved policy rules against verified domain facts and workflow state."""

    def __init__(self, retriever: PolicyRetriever, geographic_rules=None):
        self._retriever = retriever
        from ..geography import FileRulesSource
        self.geographic_rules = geographic_rules or FileRulesSource()

    def geographic_decision(self, context=None, pinned=None, query=None):
        from ..geography import evaluate_geography, GeographicDecision
        from card_chat.observability import emit_agent_trace
        evidence = []
        try:
            # Hybrid retrieval performs a genuine semantic/vector query as well
            # as mandatory ID/applicability lookup. Raw conversation is excluded.
            evidence = self._retriever.retrieve('replace_card', {}, query=query or
                'geographic international card replacement excluded countries eligibility')
            decision = evaluate_geography(context, self.geographic_rules.load(), evidence,
                                         (pinned or {}).get('POL-006'))
        except Exception:
            decision = GeographicDecision(decision='unavailable', reason_code='geographic_policy_unavailable',
                explanation='Replacement policy is unavailable. Card blocking remains available; please contact support or try a new request.')
        decision.evidence_citations = [{'policy_id': p.policy_id, 'version': p.version,
            'source': p.source, 'section_title': p.section_title, 'content_hash': p.content_hash}
            for p in evidence if p.policy_id == 'POL-006' and p.mandatory]
        emit_agent_trace('geographic_policy', country_code=decision.country_code,
            evidence_source=decision.evidence_source, outcome=decision.decision,
            reason_code=decision.reason_code, policy_id=decision.policy_id, version=decision.version,
            artifact_hash=decision.artifact_hash, rule_id=decision.rule_id,
            retrieved_policy_ids=sorted({p.policy_id for p in evidence}))
        return decision, evidence

    def validate_assistance_plan(self, plan, passages, allowed_tools):
        """An advisory plan grants no tool authority; all writes retain API guards."""
        permitted = {'block_card', 'check_digital_card_eligibility',
                     'check_emergency_cash_eligibility', 'request_specialist_handoff'}
        actions = plan.get('actions', [])
        active = self._retriever.retrieve_mandatory(policy_id='POL-005')
        governing = [e for e in active if e.policy_id == 'POL-005']
        rules_ok = (len({e.version for e in governing}) == 1 and bool(governing) and
            all(e.conditions.get('rules_version') == e.version and
                e.conditions.get('simulation_only') is True and
                e.conditions.get('return_window_hours') == 48 and
                {'never_freeze_account', 'require_card_block_confirmation',
                 'no_real_digital_issuance_or_cash_disbursement'}.issubset(e.restrictions)
                for e in governing))
        seen, results = set(), []
        for index, item in enumerate(actions):
            name = item['action']
            evidence = [p for p in passages if p['policy_id'] == item['policy_id']
                        and p['version'] == item['version'] and
                        ' '.join(item['quote'].split()) in ' '.join(p['text'].split())]
            valid = (rules_ok and name in permitted and name not in seen and bool(evidence)
                     and item['policy_id'] == 'POL-005'
                     and item['version'] == governing[0].version
                     and 'block_card' in allowed_tools
                     and actions[0]['action'] == 'block_card'
                     and (name != 'block_card' or (index == 0 and item['requires_confirmation']))
                     and (name not in ('check_digital_card_eligibility', 'check_emergency_cash_eligibility')
                          or item['requires_confirmation'] is False)
                     and (name != 'request_specialist_handoff' or
                          (item['requires_confirmation'] and index == len(actions)-1)))
            seen.add(name)
            results.append({'action': name, 'outcome': 'validated' if valid else 'rejected'})
        return results or [{'action': 'none', 'outcome': 'rejected'}]

    def evaluate(self, intent=None, user_text="", facts=None, state=None, pending_question=None,
                 last_domain_event=None, pinned_policies=None, **kwargs):
        if isinstance(user_text, dict) and facts is None:
            facts, user_text = user_text, ""
        facts = dict(facts or {})
        geographic_pending = pending_question or (state or {}).get('pending_customer_question')
        pending_kind = (geographic_pending.get('question_type') if isinstance(geographic_pending, dict)
                        else getattr(geographic_pending, 'question_type', None))
        accepting_replacement = (pending_kind == 'replacement_offer' and
            is_affirmative(str(user_text)) and not is_negative(str(user_text)))
        if intent in ('view_cards', 'view_accounts'):
            return PolicyEvaluationResult(
                permitted=True,
                decision_reason='Read-only customer data retrieval does not require a servicing policy decision.',
            )
        if intent == 'replace_card' or accepting_replacement:
            geo, geo_evidence = self.geographic_decision(
                (state or {}).get('country_context') or facts.get('country_context'), pinned_policies)
            if geo.decision not in ('allowed', 'ALLOW'):
                is_denied = geo.decision in ('denied', 'DENY')
                is_clar = geo.decision in ('clarification_required', 'REQUIRE_CLARIFICATION')
                is_approval = geo.decision in ('requires_human_approval', 'REQUIRES_HUMAN_APPROVAL')
                return PolicyEvaluationResult(
                    permitted=False,
                    prohibited=is_denied,
                    clarification_required=is_clar,
                    requires_human_approval=is_approval,
                    decision_reason=geo.explanation,
                    geographic_decision=geo.model_dump(),
                    policy_error='GeographicPolicyUnavailable' if geo.decision in ('unavailable', 'UNAVAILABLE') else None,
                    retrieved_policies=[p.model_dump() for p in geo_evidence],
                    pinned_versions={**{p.policy_id: p.version for p in geo_evidence}, **(pinned_policies or {})}
                )
        facts.setdefault("is_temporary_freeze", facts.get("temporary_freeze", False))
        facts.setdefault("is_permanently_compromised", facts.get("compromised", False))
        effective_intent = intent
        try:
            if facts.get('retrieval_failed'):
                raise RuntimeError('Verified banking facts unavailable')
            pending = pending_question or (state or {}).get("pending_customer_question")
            if pending:
                kind = pending.get("question_type") if isinstance(pending, dict) else pending.question_type
                effective_intent = "unblock_card" if kind == "unblock_offer" else "replace_card"
            if (last_domain_event or (state or {}).get("last_domain_event") or facts.get("last_domain_event")) == "card_blocked_success" and not intent:
                effective_intent = "block_card"
            try:
                evidence = self._retriever.retrieve(effective_intent, facts) if effective_intent else []
            except Exception as retrieval_error:
                if str(retrieval_error).startswith('Approved index missing or stale:'):
                    raise
                if effective_intent != 'block_card' or not hasattr(self._retriever, 'retrieve_blocking_controls'):
                    raise
                evidence = self._retriever.retrieve_blocking_controls()
            required = {"issue_card": "POL-001", "create_card": "POL-001", "unblock_card": "POL-001",
                        "replace_card": "POL-002", "block_card": "POL-003"}.get(effective_intent)
            if required and required not in {e.policy_id for e in evidence}:
                raise RuntimeError(f"Missing mandatory policy {required}")
            if required and getattr(self._retriever, 'strict_index', False) and 'POL-004' not in {e.policy_id for e in evidence}:
                raise RuntimeError('Missing mandatory policy POL-004')
            pins = dict(pinned_policies or {})
            if pending:
                pending_id = pending.get('policy_id') if isinstance(pending, dict) else pending.policy_id
                pending_version = pending.get('policy_version') if isinstance(pending, dict) else pending.policy_version
                pins.setdefault(pending_id, pending_version)
            if required:
                # An accepted offer can depend on a policy other than the tool's
                # primary policy (e.g. POL-003 offer -> POL-002 replacement).
                # Revalidate every pinned source, even if semantic search omitted it.
                for policy_id in pins:
                    if effective_intent == 'block_card' and policy_id in ('POL-002', 'POL-005', 'POL-006'):
                        continue  # Replacement version changes never prevent blocking.
                    if policy_id not in {e.policy_id for e in evidence}:
                        current = [e for e in self._retriever.retrieve_mandatory(policy_id=policy_id)
                                   if e.policy_id == policy_id]
                        if not current:
                            raise RuntimeError('Pinned approved policy is unavailable')
                        evidence.extend(current)
            for e in evidence:
                if effective_intent == 'block_card' and e.policy_id in ('POL-002', 'POL-005', 'POL-006'):
                    continue
                if e.policy_id in pins and pins[e.policy_id] != e.version:
                    raise RuntimeError(f"Approved policy version changed: {e.policy_id}; restart the workflow")
            if pending and effective_intent == "unblock_card" and facts.get("is_permanently_compromised"):
                result = PolicyEvaluationResult(permitted=False, prohibited=True,
                    decision_reason="POL-001 forbids unblocking a permanently compromised card.")
            else:
                result = self._evaluate(intent, user_text, facts, state, pending_question,
                                        last_domain_event, pinned_policies, _evidence=evidence, **kwargs)
            if evidence:
                result.retrieved_policies = [e.model_dump() for e in evidence]
                result.pinned_versions = {**{e.policy_id: e.version for e in evidence}, **pins}
                result.evidence_citations = [{"policy_id": e.policy_id, "version": e.version,
                    "source": e.source, "section_title": e.section_title} for e in evidence]
            return result
        except Exception:
            logger.exception("Policy evaluation unavailable")
            if effective_intent == 'block_card':
                return PolicyEvaluationResult(
                    permitted=True,
                    decision_reason=(
                        'Card blocking remains available as a risk-reducing security action while '
                        'servicing policy retrieval is unavailable.'
                    ),
                )
            return PolicyEvaluationResult(permitted=False, prohibited=True,
                policy_error="PolicyRetrievalUnavailable",
                decision_reason="Required approved policies are unavailable. Please try again later.")

    def evaluate_constraints(self, *args, **kwargs):
        """Live workflow uses the model for offers; retain deterministic write guards."""
        return self.evaluate(*args, **kwargs, _recommendations=False)

    def _evaluate(
        self,
        intent: str | None = None,
        user_text: str | dict[str, Any] = "",
        facts: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        pending_question: Any | None = None,
        last_domain_event: str | None = None,
        pinned_policies: dict[str, str] | None = None,
        _evidence: list[PolicyEvidence] | None = None,
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
                if not kwargs.get('_recommendations', True):
                    return PolicyEvaluationResult(permitted=True,
                        decision_reason='Card blocking confirmed; next-step recommendation pending.')
                return self._evaluate_post_block(facts, state, _evidence)
            return PolicyEvaluationResult(permitted=True, decision_reason="No intent specified")

        # 3. Retrieve policy evidence
        evidence = _evidence if _evidence is not None else self._retriever.retrieve(intent, facts)
        evidence_dicts = [e.model_dump() for e in evidence]
        pinned = {e.policy_id: e.version for e in evidence}

        # 4. Intent-specific policy evaluations
        if intent in ("issue_card", "create_card"):
            return self._evaluate_issuance(facts, state, evidence, evidence_dicts, pinned,
                                           recommendations=kwargs.get('_recommendations', True))

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

    def validate_execution(self, tool_name, arguments, facts, state=None):
        return self.evaluate(tool_name, facts=facts, state=state,
                             pinned_policies=(state or {}).get('pinned_policies', {}))

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
        if is_affirmative(latest_msg) and not is_negative(latest_msg):
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

    def _evaluate_post_block(self, facts: dict[str, Any], state: dict[str, Any], evidence=None) -> PolicyEvaluationResult:
        geo, geographic_evidence = self.geographic_decision(state.get('country_context') or facts.get('country_context'),
            state.get('pinned_policies'))
        if geo.decision != 'allowed':
            return PolicyEvaluationResult(permitted=True, decision_reason=geo.explanation,
                geographic_decision=geo.model_dump(), recommendation={'action': 'none'},
                retrieved_policies=[p.model_dump() for p in geographic_evidence])
        evidence = evidence if evidence is not None else self._retriever.retrieve_mandatory(policy_id="POL-003")
        pol3 = next((e for e in evidence if e.policy_id == "POL-003"), None)
        version = pol3.version if pol3 else "1.0.0"
        pinned = {"POL-003": version}

        block_reason = str(facts.get("last_block_reason", "lost")).lower()
        is_freeze = facts.get("is_temporary_freeze", any(term in block_reason for term in ("freeze", "frozen")))

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
        recommendations: bool = True,
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
        if (pol1 and pol1.conditions.get("allow_issuance_on_temporary_freeze")
                and facts.get("is_temporary_freeze") and not facts.get('is_permanently_compromised')):
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

        if recommendations and is_freeze:
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
