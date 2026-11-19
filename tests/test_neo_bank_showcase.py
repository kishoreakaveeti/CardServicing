"""
Neo Bank Showcase Integration Tests - December 2026 Principal Engineer Demonstration.

Validates:
- Scenario A: Lost card in Sydney (Australia inferred, replacement allowed under POL-006)
- Scenario B: Lost card in North Korea (KP inferred, block-only allowed, replacement restricted)
- Scenario C: Missing country (prompt for country selection, country_required=True)
- Scenario D: Upset customer (upset sentiment, elevated/high urgency, zero marketing cross-sell)
- Scenario E: Delighted customer (delighted sentiment, product exploration offer)
- Scenario F & G: Web Speech & Trace Telemetry contracts, zero account balance exposure,
  and direct API security enforcement.
"""
import pytest
from fastapi.testclient import TestClient

from card_chat.main import create_app
from card_chat.provider import StubModel
from card_chat.catalogue import load_catalogue
from card_chat.policy.geography import (
    declared_country,
    normalize_country,
    local_geographic_decision,
)
from card_chat.policy.engagement import (
    classify_sentiment_and_urgency,
    evaluate_engagement_policy,
)
from card_chat.policy.adapters.vector_stores.qdrant_store import QdrantVectorStore
from card_chat.policy.factory import build_policy_retriever
from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline


class ShowcaseGateway:
    def __init__(self, accounts=None, cards=None):
        self.catalogue = load_catalogue()
        self.calls = []

    async def execute(self, name, arguments, key=None, **kwargs):
        self.calls.append((name, arguments))
        if name == "block_card":
            cid = arguments.get("card_id")
            return {"status": "success", "action": "blocked", "card_id": cid}
        if name == "replace_card":
            cid = arguments.get("card_id")
            return {"status": "success", "action": "replaced", "old_card_id": cid, "new_card_id": "CARD-NEW-02"}
        if name == "create_account":
            return {"status": "success", "account_number": "ACC-999888777", "account_type": arguments.get("account_type", "transaction")}
        return {"status": "success"}

    async def declare_country(self, session_id, country_context):
        return f"GEO-BIND-{country_context.get('code')}"


@pytest.fixture
def geo_retriever(tmp_path):
    r = build_policy_retriever(vector_store=QdrantVectorStore(), config={"EMBEDDING_PROVIDER": "deterministic"})
    PolicyIngestionPipeline(r._doc_source, r._embedder, r._store, audit_log_path=tmp_path / "audit.json").ingest_all()
    r.strict_index = True
    yield r
    r._store.client.close()


def test_scenario_a_sydney_inference_and_replacement_allowed():
    """Scenario A: 'I lost my card while travelling on a train in Sydney' -> AU inferred, replacement permitted."""
    text = "I lost my card while travelling on a train in Sydney"
    decl = declared_country(text)
    assert decl is not None
    assert decl.code == "AU"
    assert decl.status == "explicit"
    assert decl.source == "customer_declaration"

    # Verify rules engine allows AU under POL-006
    decision = local_geographic_decision({"code": "AU", "status": "explicit"})
    assert decision.decision == "allowed"
    assert decision.country_code == "AU"
    assert decision.policy_id == "POL-006"


def test_scenario_b_north_korea_inference_and_replacement_denied():
    """Scenario B: 'I'm on vacation in North Korea and my card was stolen' -> KP inferred, replacement denied."""
    text = "I'm on vacation in North Korea and my card was stolen"
    decl = declared_country(text)
    assert decl is not None
    assert decl.code == "KP"
    assert decl.status == "explicit"

    # Verify rules engine denies KP under POL-006
    decision = local_geographic_decision({"code": "KP", "status": "explicit"})
    assert decision.decision == "denied"
    assert decision.country_code == "KP"
    assert decision.policy_id == "POL-006"
    assert "restricted" in decision.explanation.lower()


def test_scenario_c_missing_country_prompts_clarification():
    """Scenario C: 'I lost my card, please replace it' -> missing location prompts clarification."""
    text = "I lost my card, please replace it"
    decl = declared_country(text)
    assert decl is None  # No country mentioned

    # In chat, missing country triggers prompt and sets country_required
    decision = local_geographic_decision({"code": None, "status": "unknown"})
    assert decision.decision == "clarification_required"
    assert "Which country should your replacement card be issued for?" in decision.explanation


def test_scenario_d_upset_customer_sentiment_and_no_cross_sell():
    """Scenario D: Upset customer -> high urgency, no promotional content allowed."""
    text = "I'm stranded and furious, my card was stolen and your app is giving me errors!"
    analysis = classify_sentiment_and_urgency(text)
    assert analysis.sentiment == "upset"
    assert analysis.urgency == "high"

    policy = evaluate_engagement_policy(analysis.sentiment, analysis.urgency, workflow_completed=True, marketing_consent=True)
    assert policy.promotion_permitted is False
    assert policy.suggested_intro is not None
    assert "apologize" in policy.communication_style.lower() or "concise" in policy.communication_style.lower()


def test_scenario_e_delighted_customer_sentiment_and_product_exploration():
    """Scenario E: Delighted customer -> normal urgency, optional product exploration offer."""
    text = "Thank you so much, your service is amazing!"
    analysis = classify_sentiment_and_urgency(text)
    assert analysis.sentiment in ("delighted", "happy")
    assert analysis.urgency == "normal"

    policy = evaluate_engagement_policy(analysis.sentiment, analysis.urgency, workflow_completed=True, marketing_consent=True)
    assert policy.promotion_permitted is True
    assert policy.suggested_intro is not None


def test_neo_bank_experience_apis_zero_balance_guarantee(geo_retriever):
    """Direct API validation: GET /api/accounts must NEVER return account balances."""
    gateway = ShowcaseGateway()
    app = create_app(
        StubModel(),
        gateway,
        policy_retriever=geo_retriever,
        config={
            "LOCAL_CUSTOMER": {
                "sub": "test-user",
                "username": "alex",
                "permissions": ["tools:execute"],
                "accounts": [{"account_number": "ACC-0012345678", "account_type": "transaction"}],
            }
        },
    )
    with TestClient(app) as client:
        res = client.get("/api/accounts")
        assert res.status_code == 200
        accounts = res.json()
        assert isinstance(accounts, list)
        for acc in accounts:
            assert "balance" not in acc
            assert "account_balance" not in acc
            assert "available_balance" not in acc
            assert "current_balance" not in acc
            assert "account_type" in acc


def test_neo_bank_cards_api_masked_pan_and_security(geo_retriever):
    """Direct API validation: GET /api/cards returns masked PAN and never exposes full PAN, CVV, or PIN."""
    gateway = ShowcaseGateway()
    app = create_app(
        StubModel(),
        gateway,
        policy_retriever=geo_retriever,
        config={
            "LOCAL_CUSTOMER": {
                "sub": "test-user",
                "username": "alex",
                "permissions": ["tools:execute"],
                "cards": [
                    {
                        "id": "CARD-SYDNEY-01",
                        "account_number": "ACC-0012345678",
                        "card_number": "4532000011112222",
                        "cardholder_name": "Alex Mercer",
                        "expiry_month": 12,
                        "expiry_year": 2028,
                        "status": "active",
                    }
                ],
            }
        },
    )
    with TestClient(app) as client:
        res = client.get("/api/cards")
        assert res.status_code == 200
        cards = res.json()
        assert isinstance(cards, list)
        for card in cards:
            assert "cvv" not in card
            assert "pin" not in card
            assert "card_number" in card
            masked = card["card_number"]
            assert "••••" in masked or "****" in masked or len(masked) <= 12


def test_neo_bank_chat_trace_telemetry_contract(geo_retriever):
    """Verify conversational responses include structured trace telemetry with latencies and token counts."""
    gateway = ShowcaseGateway()
    app = create_app(
        StubModel(),
        gateway,
        policy_retriever=geo_retriever,
        config={"LOCAL_CUSTOMER": {"sub": "test-user", "username": "alex", "permissions": ["tools:execute"]}},
    )
    with TestClient(app) as client:
        conv_res = client.post("/conversations")
        cid = conv_res.json()["id"]

        msg_res = client.post(
            f"/conversations/{cid}/messages",
            json={"text": "I lost my card while travelling on a train in Sydney"},
        )
        assert msg_res.status_code == 200
        data = msg_res.json()
        assert "trace" in data
        trace = data["trace"]
        assert "telemetry" in trace
        assert "e2e_latency_ms" in trace["telemetry"]
        assert "total_tokens" in trace["telemetry"]
        assert "intent" in trace
        assert "customer_request" in trace
        assert "policy_evaluation" in trace
        assert "country_inference" in trace
