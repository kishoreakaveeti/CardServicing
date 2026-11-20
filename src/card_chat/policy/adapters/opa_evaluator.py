"""Open Policy Agent (OPA) adapter for deterministic banking policy enforcement."""
from __future__ import annotations

import logging
import os
from typing import Any
import httpx

from ..geography import (
    CountryContext,
    GeographicDecision,
    GeographicRules,
    evaluate_geography,
    normalize_country,
)
from ..models import PolicyEvidence

logger = logging.getLogger(__name__)


class OPAGeographicEvaluator:
    """Evaluates geographic replacement rules deterministically, via OPA container or local engine."""

    def __init__(self, opa_url: str | None = None):
        self.opa_url = (opa_url or os.getenv("OPA_URL", "")).rstrip("/")

    def evaluate(
        self,
        context: dict[str, Any] | CountryContext,
        rules: GeographicRules,
        evidence: list[PolicyEvidence],
        pinned_version: str | None = None,
    ) -> GeographicDecision:
        """Evaluate geographic rules through OPA HTTP endpoint if available, falling back to local engine."""
        # Always run consistency checks first
        ctx = CountryContext.model_validate(context or {})
        if self.opa_url:
            try:
                decision = self._evaluate_remote(ctx, rules, evidence, pinned_version)
                if decision:
                    return decision
            except Exception as exc:
                logger.warning("OPA query failed (%s); evaluating with deterministic local engine", exc)

        return evaluate_geography(ctx.model_dump(), rules, evidence, pinned_version)

    def _evaluate_remote(
        self,
        ctx: CountryContext,
        rules: GeographicRules,
        evidence: list[PolicyEvidence],
        pinned_version: str | None = None,
    ) -> GeographicDecision | None:
        """Query OPA REST API data endpoint."""
        endpoint = f"{self.opa_url}/v1/data/banking/geographic"
        payload = {
            "input": {
                "country_code": ctx.code,
                "country_status": ctx.status,
                "rules": {
                    "home_country_code": rules.home_country_code,
                    "excluded_country_codes": rules.excluded_country_codes,
                    "overseas_replacement_requires_fincrime_approval": rules.overseas_replacement_requires_fincrime_approval,
                    "domestic_replacement_requires_fincrime_approval": rules.domestic_replacement_requires_fincrime_approval,
                    "unknown_country_action": rules.unknown_country_action,
                    "security_card_blocking_allowed": rules.security_card_blocking_allowed,
                },
            }
        }
        with httpx.Client(timeout=2.0) as client:
            resp = client.post(endpoint, json=payload)
            if resp.status_code != 200:
                logger.warning("OPA responded with HTTP %d: %s", resp.status_code, resp.text)
                return None
            result_data = resp.json().get("result", {})
            decision_val = result_data.get("decision", "UNAVAILABLE")
            reason_code = result_data.get("reason_code", "policy_unavailable")
            is_overseas = result_data.get("is_overseas", False)

            # Map to canonical GeographicDecision
            dec_map = {
                "ALLOW": "allowed",
                "DENY": "denied",
                "REQUIRES_HUMAN_APPROVAL": "requires_human_approval",
                "REQUIRE_CLARIFICATION": "clarification_required",
            }
            mapped_decision = dec_map.get(decision_val.upper(), decision_val.lower())
            explanation = ""
            if mapped_decision == "denied":
                explanation = "Replacement is restricted from your declared location under our synthetic demonstration policy. You can still block your card securely and contact specialist support."
            elif mapped_decision == "requires_human_approval":
                explanation = "Replacement card request for an overseas location requires FinCrime staff approval before issuance. Your request will be reviewed by an authorized analyst."
            elif mapped_decision == "clarification_required":
                explanation = "Which country should your replacement card be issued for? Your declaration is required before replacement can be reviewed. Card blocking remains available."
            elif mapped_decision == "allowed":
                explanation = "Your declared country is eligible under the demonstration geographic policy; other replacement controls still apply."

            return GeographicDecision(
                action="replace_card",
                country_code=ctx.code,
                decision=mapped_decision,
                is_overseas=is_overseas,
                policy_id=rules.policy_id,
                version=rules.version,
                rules_version=rules.version,
                reason_code=reason_code,
                explanation=explanation,
                review_requirements=["FINCRIME_OVERSEAS_APPROVAL"] if mapped_decision == "requires_human_approval" else [],
            )

    def sync_rules(self, rules: GeographicRules) -> bool:
        """Sync updated rules into OPA data store."""
        if not self.opa_url:
            return False
        endpoint = f"{self.opa_url}/v1/data/banking/geographic/rules"
        data = {
            "home_country_code": rules.home_country_code,
            "excluded_country_codes": rules.excluded_country_codes,
            "overseas_replacement_requires_fincrime_approval": rules.overseas_replacement_requires_fincrime_approval,
            "domestic_replacement_requires_fincrime_approval": rules.domestic_replacement_requires_fincrime_approval,
            "unknown_country_action": rules.unknown_country_action,
            "security_card_blocking_allowed": rules.security_card_blocking_allowed,
        }
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.put(endpoint, json=data)
                return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to sync rules to OPA at %s: %s", endpoint, exc)
            return False
