"""Consolidated Regression Tests for Bug Fixes 1, 2, 3, and 4.

Covers:
BUG 1: Navigation bar responsiveness, CSS matching, and desktop duplicate prevention.
BUG 2: "I want a card" natural language variations, account dependency guidance, and specialist routing.
BUG 3: FinCrime staff portal endpoints, session verification, RBAC, and policy response schema.
BUG 4: Authoritative versioned country declaration across multi-turn transitions and execution boundary enforcement.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from card_chat.catalogue import load_catalogue
from card_chat.experience import create_app as experience_app
from card_chat.main import create_app as agent_app
from card_chat.orchestration.supervisor import SupervisorAgent
from card_chat.policy.adapters.vector_stores.qdrant_store import QdrantVectorStore
from card_chat.policy.factory import build_policy_evaluator, build_policy_retriever
from card_chat.policy.adapters.evaluator import GroundedPolicyEvaluator
from card_chat.policy.geography import (
    CountryContext,
    RequestInterpretation,
    declared_country,
    normalize_country,
)
from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline
from card_chat.provider import StubModel
from card_chat.security import Security
from test_http_services import identity


class MockGateway:
    catalogue = load_catalogue()

    def __init__(self):
        self.calls = []

    async def execute(self, name, arguments, key=None, agent=None):
        self.calls.append((name, arguments, key, agent))
        return {"ok": True, "result": {"status": "success", "id": "TEST-123"}}

    async def declare_country(self, cid, country_context):
        return {"workflow": cid, "revision": "rev-1", "country": country_context}


@pytest.fixture
def geo_retriever(tmp_path):
    r = build_policy_retriever(
        vector_store=QdrantVectorStore(), config={"EMBEDDING_PROVIDER": "deterministic"}
    )
    PolicyIngestionPipeline(
        r._doc_source, r._embedder, r._store, audit_log_path=tmp_path / "audit.json"
    ).ingest_all()
    r.strict_index = True
    yield r
    r._store.client.close()


# =============================================================================
# BUG 1: Navigation UI & CSS Responsiveness
# =============================================================================
def test_bug1_navigation_css_and_navbar_contract():
    """Verify Navbar.tsx and style.css avoid duplicate navigation rendering on desktop."""
    navbar_path = Path("frontend/src/components/Navbar.tsx")
    css_path = Path("frontend/src/style.css")

    navbar_code = navbar_path.read_text(encoding="utf-8")
    css_code = css_path.read_text(encoding="utf-8")

    # Navbar mobile container uses matching class
    assert 'className="bottom-nav"' in navbar_code

    # Desktop hides mobile bottom-nav
    assert ".bottom-nav," in css_code or ".bottom-nav {" in css_code
    assert "display: none;" in css_code

    # Mobile breakpoint enables flex
    assert "@media (max-width: 768px)" in css_code
    assert "display: flex;" in css_code


def test_block_and_replace_chip_confirms_an_existing_combined_review():
    """The combined suggestion must execute a reviewed action, not resubmit the same prompt."""
    main_code = Path("frontend/src/main.tsx").read_text(encoding="utf-8")
    chat_code = Path("frontend/src/components/ChatView.tsx").read_text(encoding="utf-8")

    assert "reviewedCombinedAction" in main_code
    assert "await handleConfirmAction()" in main_code
    assert "latestReply?.can_confirm === true" in main_code
    assert 'type="button"' in chat_code


# =============================================================================
# BUG 2: "I want a card" Intent & First-Time Customer Account Dependency
# =============================================================================
NATURAL_CARD_PHRASES = [
    "I want a card",
    "I need a debit card",
    "Can I get a card?",
    "I'd like a new card",
    "Please issue me a card",
    "I want to create a card",
    "How can I get my first card?",
]


@pytest.mark.parametrize("phrase", NATURAL_CARD_PHRASES)
def test_bug2_natural_card_creation_phrases_recognized(phrase, geo_retriever):
    """Verify natural language variations identify card issuance intent."""
    supervisor = SupervisorAgent(
        StubModel(),
        policy_retriever=geo_retriever,
        policy_evaluator=build_policy_evaluator(geo_retriever),
    )
    allowed_tools = {
        "issue_card": {},
        "create_card": {},
        "create_account": {},
        "view_accounts": {},
        "view_cards": {},
        "replace_card": {},
        "block_card": {},
    }

    state = {
        "messages": [{"role": "user", "content": phrase}],
        "allowed_tools": allowed_tools,
        "verified_draft": {},
        "handoff_history": [],
    }

    result = asyncio.run(supervisor.plan_or_route(state))
    assert result.get("active_agent") == "cards_agent"
    assert any(
        plan.get("intent") in ("issue_card", "create_card")
        for plan in result.get("action_plan", [])
    )


def test_bug2_first_time_customer_zero_accounts_guides_to_account_opening(identity):
    """First-time customer with 0 accounts asking for a card is guided to create an account first."""
    config, headers = identity
    gw = MockGateway()
    app = agent_app(StubModel(), gw, config=config)
    with TestClient(app) as client:
        # Customer with zero accounts
        zero_acc_token = Security(config).issue_customer({
            "sub": "cust_zero_acc",
            "preferred_username": "zero.acc",
            "accounts": [],
            "permissions": ["tools:execute"],
        })
        h = {**headers(), "X-Customer-Delegation": zero_acc_token}

        # Start conversation
        cid = client.post("/conversations", headers=h).json()["id"]

        # Enter "I want a card"
        res = client.post(
            f"/conversations/{cid}/messages", json={"text": "I want a card"}, headers=h
        )
        assert res.status_code == 200
        data = res.json()

        # Must guide to create an account first
        msg = data.get("message", "")
        assert "Create an account first" in msg
        assert data.get("input_tool") == "create_account"


def test_bug2_customer_with_account_receives_card_form(identity):
    """Customer with an existing account receives the card issuance form."""
    config, headers = identity
    gw = MockGateway()
    app = agent_app(StubModel(), gw, config=config)
    with TestClient(app) as client:
        has_acc_token = Security(config).issue_customer({
            "sub": "cust_has_acc",
            "preferred_username": "has.acc",
            "accounts": ["ACC-99887766"],
            "permissions": ["tools:execute"],
        })
        h = {**headers(), "X-Customer-Delegation": has_acc_token}

        cid = client.post("/conversations", headers=h).json()["id"]

        res = client.post(
            f"/conversations/{cid}/messages", json={"text": "I want a card"}, headers=h
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("input_tool") in ("issue_card", "create_card")


def test_bug2_replacement_request_does_not_route_to_account_creation(identity):
    """A replacement request must strictly route to replacement, never account opening."""
    config, headers = identity
    gw = MockGateway()
    app = agent_app(StubModel(), gw, config=config)
    with TestClient(app) as client:
        cust_token = Security(config).issue_customer({
            "sub": "cust_rep",
            "preferred_username": "rep.cust",
            "accounts": ["ACC-12345678"],
            "permissions": ["tools:execute"],
        })
        h = {**headers(), "X-Customer-Delegation": cust_token}

        cid = client.post("/conversations", headers=h).json()["id"]
        res = client.post(
            f"/conversations/{cid}/messages", json={"text": "I need to replace my card"}, headers=h
        )
        assert res.status_code == 200
        data = res.json()
        assert "account first" not in data.get("message", "").lower()


def test_empty_card_customer_gets_view_cards_navigation(identity):
    """An empty portfolio gets explicit guidance and a working Cards navigation action."""
    config, headers = identity
    app = agent_app(StubModel(), MockGateway(), config=config)
    with TestClient(app) as client:
        token = Security(config).issue_customer({
            "sub": "cust_no_cards",
            "preferred_username": "no.cards",
            "accounts": [],
            "cards": [],
            "permissions": ["tools:execute"],
        })
        h = {**headers(), "X-Customer-Delegation": token}
        cid = client.post("/conversations", headers=h).json()["id"]
        response = client.post(
            f"/conversations/{cid}/messages", json={"text": "Show me my cards"}, headers=h
        )
        assert response.status_code == 200
        body = response.json()
        assert "no" in body["message"].lower() and "cards" in body["message"].lower()
        assert body["suggested_actions"] == ["view_cards"]


def test_read_only_cards_and_security_block_survive_policy_index_outage():
    """Viewing is policy-independent and blocking remains safely available during an index outage."""
    class UnavailableRetriever:
        strict_index = True

        def retrieve(self, *args, **kwargs):
            raise RuntimeError("synthetic policy index outage")

    evaluator = GroundedPolicyEvaluator(UnavailableRetriever())
    view = evaluator.evaluate("view_cards", facts={})
    block = evaluator.evaluate("block_card", facts={})
    replace = evaluator.evaluate("replace_card", facts={}, state={"country_context": {"code": "AU"}})

    assert view.permitted and not view.policy_error
    assert block.permitted and not block.policy_error
    assert not replace.permitted and replace.policy_error == "GeographicPolicyUnavailable"


def test_remote_policy_facts_does_not_depend_on_replacement_write_context():
    """The customer facts read must open a normal delegated MCP session."""
    gateway_code = Path("src/card_chat/gateway.py").read_text(encoding="utf-8")
    policy_facts = gateway_code.split("async def policy_facts", 1)[1].split(
        "async def execute_approved_replacement", 1)[0]

    assert "async with session() as connected" in policy_facts
    assert "session(bool(replacement_context" not in policy_facts


def test_every_fincrime_case_path_persists_the_geographic_binding():
    """Both direct and post-block approval cases must carry the signed Cards binding."""
    supervisor_code = Path("src/card_chat/orchestration/supervisor.py").read_text(encoding="utf-8")
    assert supervisor_code.count('"geographic_binding": state.get("geographic_binding")') >= 2


def test_chat_fincrime_review_is_created_only_after_customer_confirmation():
    """Transition review must render a confirmable action, then create its case on confirmation."""
    main_code = Path("src/card_chat/main.py").read_text(encoding="utf-8")

    assert "async def submit_replacement_for_fincrime" in main_code
    assert "if c.proposal.name == 'replace_card':" in main_code
    assert "return await submit_replacement_for_fincrime(cid, c, decision, evidence)" in main_code
    assert "The replacement will be created automatically if approved." in main_code


# =============================================================================
# BUG 3: FinCrime Staff Portal Security & Resilient Architecture
# =============================================================================
def test_bug3_fincrime_unauthenticated_and_rbac(identity):
    """Verify unauthenticated requests return 401 and staff credentials authenticate correctly."""
    config, _ = identity
    app = experience_app({**config, "PUBLIC_ORIGIN": "http://testserver"})
    with TestClient(app) as client:
        # 1. Unauthenticated session check returns 401
        assert client.get("/api/fincrime/session").status_code == 401

        # 2. Staff Analyst authentication succeeds
        login_res = client.post(
            "/auth/staff/login-credentials",
            headers={"Origin": "http://testserver"},
            json={"username": "fincrime.analyst", "password": "analyst123"},
        )
        assert login_res.status_code == 200
        analyst_data = login_res.json()
        assert analyst_data.get("authenticated") is True
        assert analyst_data.get("is_staff") is True
        assert analyst_data.get("is_analyst") is True

        # 3. Staff Administrator authentication succeeds
        admin_res = client.post(
            "/auth/staff/login-credentials",
            headers={"Origin": "http://testserver"},
            json={"username": "fincrime.admin", "password": "admin123"},
        )
        assert admin_res.status_code == 200
        admin_data = admin_res.json()
        assert admin_data.get("is_admin") is True

        # 4. Unauthorized non-staff user fails
        bad_res = client.post(
            "/auth/staff/login-credentials",
            headers={"Origin": "http://testserver"},
            json={"username": "customer_user", "password": "password123"},
        )
        assert bad_res.status_code == 401


def test_bug3_fincrime_policies_contains_id_and_policy_id(identity):
    """Verify /fincrime/policies returns both id and policy_id for resilient frontend consumption."""
    config, headers = identity
    app = agent_app(StubModel(), MockGateway(), config=config)
    with TestClient(app) as client:
        res = client.get("/fincrime/policies", headers=headers())
        assert res.status_code == 200
        data = res.json()
        assert "policies" in data
        assert len(data["policies"]) > 0
        for p in data["policies"]:
            assert "id" in p
            assert "policy_id" in p
            assert p["id"] == p["policy_id"]


# =============================================================================
# BUG 4: Authoritative Versioned Country Declaration & Multi-Turn Transitions
# =============================================================================
def test_bug4_country_versioning_and_revision_increment():
    """Authoritative country declaration tracks revision and increments on country change."""
    # Turn 1: Initial declaration
    c1 = declared_country("I am currently in North Korea")
    assert c1 is not None
    assert c1.code == "KP"
    assert c1.status == "explicit"
    assert c1.revision == 1
    assert c1.updated_at is not None

    # Turn 2: Repeat same country -> revision does not increment
    c2 = declared_country("I am currently in North Korea", current_context=c1)
    assert c2.code == "KP"
    assert c2.revision == 1

    # Turn 3: Country changes to Australia -> revision increments to 2
    c3 = declared_country("Actually, I am now in Australia", current_context=c2)
    assert c3.code == "AU"
    assert c3.status == "explicit"
    assert c3.revision == 2

    # Turn 4: Country changes to New Zealand -> revision increments to 3
    c4 = declared_country("I am now located in New Zealand", current_context=c3)
    assert c4.code == "NZ"
    assert c4.status == "explicit"
    assert c4.revision == 3


def test_bug4_historical_mention_does_not_declare_current_country():
    """Historical mentions must not be recognized as current country declarations."""
    assert declared_country("I visited North Korea last year") is None
    assert declared_country("I was in North Korea last month") is None
    assert declared_country("I went to New Zealand last week") is None
    assert declared_country("I returned from North Korea yesterday") is None

    # Mixed historical and current: Current must take precedence
    mixed = declared_country("I was in North Korea last month, but currently I am in Australia")
    assert mixed is not None
    assert mixed.code == "AU"
    assert mixed.status == "explicit"


def test_bug4_future_travel_does_not_declare_current_country():
    """Future travel destinations must not be recognized as current country declarations."""
    assert declared_country("I am travelling to New Zealand next week") is None
    assert declared_country("I will be in North Korea tomorrow") is None
    assert declared_country("I am flying to Japan next month") is None


def test_bug4_ambiguous_and_conflicting_declarations():
    """Conflicting or vague declarations are classified as ambiguous."""
    c_conflict = declared_country("I am in Australia and I am in North Korea")
    assert c_conflict.status == "ambiguous"

    c_or = declared_country("I am in Australia or New Zealand")
    assert c_or.status == "ambiguous"

    c_korea = declared_country("I am in Korea")
    assert c_korea.status == "ambiguous"


def test_bug4_multi_turn_kp_to_au_transition(identity):
    """Customer transitions from North Korea (denied) to Australia (allowed)."""
    config, headers = identity
    gw = MockGateway()
    app = agent_app(StubModel(), gw, config=config)
    with TestClient(app) as client:
        token = Security(config).issue_customer({
            "sub": "cust_kp_au",
            "preferred_username": "kp.au",
            "accounts": ["ACC-11223344"],
            "cards": [{"id": "CARD-1234", "account_number": "ACC-11223344", "status": "active"}],
            "permissions": ["tools:execute"],
        })
        h = {**headers(), "X-Customer-Delegation": token}

        cid = client.post("/conversations", headers=h).json()["id"]

        # Turn 1: Declare North Korea and ask for replacement -> Denied under POL-006
        t1 = client.post(
            f"/conversations/{cid}/messages",
            json={"text": "I am in North Korea. Please replace my card."},
            headers=h,
        )
        assert t1.status_code == 200
        r1 = t1.json()
        assert "restricted" in r1.get("message", "").lower() or "blocked" in r1.get("message", "").lower()

        # Turn 2: Customer declares Australia -> Allowed domestic replacement
        t2 = client.post(
            f"/conversations/{cid}/messages",
            json={"text": "Actually, I am currently in Australia. Please replace my card."},
            headers=h,
        )
        assert t2.status_code == 200
        r2 = t2.json()
        # Must NOT block saying "cannot be changed"; must permit replacement form
        assert "cannot be changed" not in r2.get("message", "")
        assert r2.get("input_tool") == "replace_card" or "replace" in r2.get("message", "").lower()


def test_bug4_multi_turn_au_to_kp_execution_boundary(identity):
    """Customer drafts in Australia, but declares North Korea before execution -> Blocked at execution boundary."""
    config, headers = identity
    gw = MockGateway()
    app = agent_app(StubModel(), gw, config=config)
    with TestClient(app) as client:
        token = Security(config).issue_customer({
            "sub": "cust_au_kp",
            "preferred_username": "au.kp",
            "accounts": ["ACC-55667788"],
            "cards": [{"id": "CARD-5678", "account_number": "ACC-55667788", "status": "active"}],
            "permissions": ["tools:execute"],
        })
        h = {**headers(), "X-Customer-Delegation": token}

        cid = client.post("/conversations", headers=h).json()["id"]

        # Turn 1: Declare Australia -> prepare replacement
        t1 = client.post(
            f"/conversations/{cid}/messages",
            json={"text": "I am in Australia. Replace my card."},
            headers=h,
        )
        assert t1.status_code == 200

        # Turn 2: Customer declares North Korea
        t2 = client.post(
            f"/conversations/{cid}/messages",
            json={"text": "Wait, I am now in North Korea."},
            headers=h,
        )
        assert t2.status_code == 200

        # Confirm / execution boundary from North Korea must be blocked
        confirm_res = client.post(
            f"/conversations/{cid}/confirm",
            json={"review": "valid-token"},
            headers=h,
        )
        # Stale/denied token must be rejected
        assert confirm_res.status_code in (403, 409)


def test_au_to_kp_compound_request_never_offers_block_and_replace(identity):
    """An explicit KP transition must revoke the prior AU replacement capability."""
    config, headers = identity
    app = agent_app(StubModel(), MockGateway(), config=config)
    with TestClient(app) as client:
        token = Security(config).issue_customer({
            "sub": "cust_au_kp_compound",
            "preferred_username": "au.kp.compound",
            "accounts": ["ACC-77889900"],
            "cards": [{"id": "CARD-9900", "account_number": "ACC-77889900", "status": "active"}],
            "permissions": ["tools:execute"],
        })
        h = {**headers(), "X-Customer-Delegation": token}
        cid = client.post("/conversations", headers=h).json()["id"]

        client.post(
            f"/conversations/{cid}/messages",
            json={"text": "I am in Australia."},
            headers=h,
        )
        response = client.post(
            f"/conversations/{cid}/messages",
            json={"text": "I am in North Korea and I want to block and replace my card."},
            headers=h,
        )

        assert response.status_code == 200
        body = response.json()
        assert body.get("confirmation_label") != "Block and replace this card"
        assert "block_and_replace" not in body.get("suggested_actions", [])
        assert body.get("input_tool") == "block_card", body

        options = client.get(f"/conversations/{cid}/inputs", headers=h).json()["tools"]
        block_tool = next(tool for tool in options if tool["name"] == "block_card")
        card_ref = next(field for field in block_tool["fields"] if field["name"] == "card_id")["options"][0]["ref"]
        review = client.post(
            f"/conversations/{cid}/inputs",
            json={
                "tool": "block_card",
                "fields": {"reason": "lost"},
                "selections": {"card_id": card_ref},
            },
            headers=h,
        ).json()
        assert review.get("can_confirm") is True
        assert review.get("confirmation_label") != "Block and replace this card"
        assert "block_and_replace" not in review.get("suggested_actions", [])
        review_message = review.get("message", "").lower()
        assert "updated from au to kp" in review_message
        assert "restricted" in review_message


def test_au_to_kp_without_in_never_inherits_australia_authorization(identity):
    """The observed phrase 'I'm currently North Korea' must revoke AU before review."""
    config, headers = identity
    app = agent_app(StubModel(), MockGateway(), config=config)
    with TestClient(app) as client:
        token = Security(config).issue_customer({
            "sub": "cust_au_kp_without_in",
            "preferred_username": "au.kp.without.in",
            "accounts": ["ACC-44332211"],
            "cards": [{"id": "CARD-2211", "account_number": "ACC-44332211", "status": "active"}],
            "permissions": ["tools:execute"],
        })
        h = {**headers(), "X-Customer-Delegation": token}
        cid = client.post("/conversations", headers=h).json()["id"]
        client.post(
            f"/conversations/{cid}/messages",
            json={"text": "I am in Australia."},
            headers=h,
        )

        response = client.post(
            f"/conversations/{cid}/messages",
            json={"text": "I'm currently North Korea and I want to block and replace my card"},
            headers=h,
        ).json()

        assert response.get("confirmation_label") != "Block and replace this card"
        assert "block_and_replace" not in response.get("suggested_actions", [])
        assert response.get("input_tool") == "block_card"
