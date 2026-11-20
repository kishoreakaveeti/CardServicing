from datetime import datetime, timedelta, timezone

from card_chat.policy.geography import CountryContext, GeographicRules, evaluate_geography
from card_chat.policy.models import PolicyEvidence


def _evidence(rules):
    import hashlib
    content_hash = hashlib.sha256(b"policy").hexdigest()
    return [PolicyEvidence(policy_id="POL-006", version=rules.version, title="Geographic",
        section_title="rules", content="policy", source="test", mandatory=True,
        conditions=rules.model_dump(exclude={"policy_id"}), content_hash=content_hash)]


def test_recent_excluded_to_permitted_transition_requires_review():
    rules = GeographicRules(version="1.0.0", excluded_country_codes=["KP"])
    context = CountryContext(code="AU", status="explicit", previous_code="KP",
        previous_updated_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    decision = evaluate_geography(context.model_dump(), rules, _evidence(rules))
    assert decision.decision == "requires_human_approval"
    assert decision.reason_code == "RECENT_EXCLUDED_COUNTRY_TRANSITION"


def test_transition_expiry_and_disable_restore_normal_domestic_rules():
    for enabled, age in ((True, 73), (False, 1)):
        rules = GeographicRules(version="1.0.0", excluded_country_codes=["KP"],
            transition_review_enabled=enabled, transition_review_lookback_hours=72)
        context = CountryContext(code="AU", status="explicit", previous_code="KP",
            previous_updated_at=(datetime.now(timezone.utc) - timedelta(hours=age)).isoformat())
        decision = evaluate_geography(context.model_dump(), rules, _evidence(rules))
        assert decision.decision == "allowed"


def test_current_excluded_country_always_precedes_transition_review():
    rules = GeographicRules(version="1.0.0", excluded_country_codes=["KP"])
    context = CountryContext(code="KP", status="explicit", previous_code="AU",
        previous_updated_at=datetime.now(timezone.utc).isoformat())
    decision = evaluate_geography(context.model_dump(), rules, _evidence(rules))
    assert decision.decision == "denied"
