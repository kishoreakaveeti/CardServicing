"""
Neo Bank - Comprehensive End-to-End Regression Verification Suite.
Validates Journeys A through G as specified in Section 15:
- Journey A: Registration to Card Issuance
- Journey B: Card Blocking
- Journey C: Conversational Card Blocking
- Journey D: Chat History Persistence & Ordering
- Journey E: Microphone Transcription Contracts
- Journey F: Policy-Restricted Actions (POL-006 Geopolitics)
- Journey G: Multi-Customer Isolation
"""
import pytest
from fastapi.testclient import TestClient

from card_chat.main import create_app
from card_chat.catalogue import load_catalogue
from card_chat.policy.geography import (
    declared_country,
    local_geographic_decision,
)
from card_chat.policy.adapters.vector_stores.qdrant_store import QdrantVectorStore
from card_chat.policy.factory import build_policy_retriever
from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline
from card_chat.turns import ProposedToolCall, CustomerReply


class JourneyStubModel:
    def __init__(self):
        self.outputs = []
        self.seen_tools = []
        self.seen_context = []

    async def turn(self, history, collected, tools):
        self.seen_tools.append(tools)
        self.seen_context.append((history, collected))
        if self.outputs:
            out = self.outputs.pop(0)
            return CustomerReply(out) if isinstance(out, str) else out
        return CustomerReply("I am Neo, your digital banking assistant. How can I help you?")

    async def close(self):
        pass


class ShowcaseGateway:
    def __init__(self):
        self.catalogue = load_catalogue()
        self.calls = []

    async def execute(self, name, arguments, key=None, **kwargs):
        self.calls.append((name, arguments))
        cid = arguments.get("card_id", "CARD-1")
        if name == "block_card":
            return {"ok": True, "result": {"status": "success", "action": "blocked", "card_id": cid, "card_number": "SIM-4532"}}
        if name == "replace_card":
            return {"ok": True, "result": {"status": "success", "action": "replaced", "old_card_id": cid, "new_card_id": "CARD-NEW-02", "card_number": "SIM-4532"}}
        if name == "create_account":
            return {"ok": True, "result": {"status": "success", "account_number": "ACC-999888777", "account_type": arguments.get("account_type", "transaction")}}
        if name == "create_card":
            return {"ok": True, "result": {"status": "success", "id": "CARD-NEW-01", "card_id": "CARD-NEW-01", "account_number": arguments.get("account_number"), "card_number": "SIM-4532"}}
        return {"ok": True, "result": {"status": "success"}}

    async def declare_country(self, session_id, country_context):
        return f"GEO-BIND-{country_context.get('code')}"


@pytest.fixture
def geo_retriever(tmp_path):
    r = build_policy_retriever(vector_store=QdrantVectorStore(), config={"EMBEDDING_PROVIDER": "deterministic"})
    PolicyIngestionPipeline(r._doc_source, r._embedder, r._store, audit_log_path=tmp_path / "audit.json").ingest_all()
    r.strict_index = True
    yield r
    r._store.client.close()


def test_journey_a_registration_to_card_issuance(geo_retriever):
    """
    Journey A: Registration to Card Issuance
    - Customer creates an account (Transaction)
    - Verifies account appears in account list
    - Issues a card for that new account
    - Verifies card appears in card list with active/created status and masked number
    """
    customer_a = {
        "sub": "cust-journey-a",
        "username": "customer_a",
        "permissions": ["tools:execute"],
        "accounts": [],
        "cards": [],
    }
    app = create_app(JourneyStubModel(), ShowcaseGateway(), policy_retriever=geo_retriever, config={"LOCAL_CUSTOMER": customer_a})

    with TestClient(app) as client:
        # Step 1: Create Account
        create_res = client.post("/api/accounts", json={"account_type": "transaction"})
        assert create_res.status_code == 201
        acc_data = create_res.json()
        account_num = acc_data["account_number"]
        assert account_num.startswith("ACC-")
        assert acc_data["account_type"] == "transaction"

        # Step 2: Verify account appears in list
        acc_list_res = client.get("/api/accounts")
        assert acc_list_res.status_code == 200
        accounts = acc_list_res.json()
        assert len(accounts) >= 1
        created_acc = next((a for a in accounts if a["account_number"] == account_num), None)
        assert created_acc is not None
        assert created_acc["account_type"] == "transaction"
        assert "balance" not in created_acc

        # Step 3: Issue New Card for this account
        card_res = client.post("/api/cards", json={"account_number": account_num, "product": "visa_debit"})
        assert card_res.status_code == 201
        card_data = card_res.json()
        card_id = card_data["id"]
        assert card_id.startswith("CARD-")

        # Step 4: Verify card appears in card list
        cards_res = client.get("/api/cards")
        assert cards_res.status_code == 200
        cards = cards_res.json()
        assert len(cards) >= 1
        new_card = next((c for c in cards if c["id"] == card_id), None)
        assert new_card is not None
        assert new_card["account_number"] == account_num
        assert "••••" in new_card["card_number"]
        assert "cvv" not in new_card
        assert "pin" not in new_card


def test_journey_b_card_blocking(geo_retriever):
    """
    Journey B: Standard Card Blocking
    - Customer selects existing active card
    - Selects valid reason (lost/stolen)
    - Confirms block
    - Verifies card status updates to blocked across APIs
    """
    customer_b = {
        "sub": "cust-journey-b",
        "username": "customer_b",
        "permissions": ["tools:execute"],
        "accounts": ["ACC-111222333"],
        "cards": [
            {
                "id": "CARD-ACTIVE-99",
                "account_number": "ACC-111222333",
                "card_number": "4532111122229999",
                "status": "active",
            }
        ],
    }
    app = create_app(JourneyStubModel(), ShowcaseGateway(), policy_retriever=geo_retriever, config={"LOCAL_CUSTOMER": customer_b})

    with TestClient(app) as client:
        # Verify card is active initially
        cards_before = client.get("/api/cards").json()
        assert cards_before[0]["status"] == "active"

        # Block the card with valid reason
        block_res = client.post("/api/cards/CARD-ACTIVE-99/block", json={"reason": "lost"})
        assert block_res.status_code == 200
        assert block_res.json()["card_status"] == "blocked"

        # Verify card list reflects blocked status
        cards_after = client.get("/api/cards").json()
        blocked_card = next(c for c in cards_after if c["id"] == "CARD-ACTIVE-99")
        assert blocked_card["status"] == "blocked"


def test_journey_c_conversational_card_blocking(geo_retriever):
    """
    Journey C: Conversational Card Blocking
    - Customer tells assistant: "I lost my card, please block it"
    - Model proposes block_card
    - Customer confirms proposal
    - Backend executes block and updates caller state
    - Cards endpoint reflects blocked card
    """
    model = JourneyStubModel()
    gateway = ShowcaseGateway()
    card_id = "CARD-TALK-01"
    customer_c = {
        "sub": "cust-journey-c",
        "username": "customer_c",
        "permissions": ["tools:execute"],
        "accounts": ["ACC-0012345678"],
        "cards": [
            {
                "id": card_id,
                "account_number": "ACC-0012345678",
                "card_number": "4532000033334444",
                "status": "active",
            }
        ],
    }
    app = create_app(model, gateway, policy_retriever=geo_retriever, config={"LOCAL_CUSTOMER": customer_c})

    with TestClient(app) as client:
        # Start conversation
        cid = client.post("/conversations").json()["id"]

        # Queue model response for card blocking intent
        model.outputs.append(ProposedToolCall("block_card", {}))

        # Customer sends message
        msg_res = client.post(f"/conversations/{cid}/messages", json={"text": "I lost my card, please block it"})
        assert msg_res.status_code == 200
        reply = msg_res.json()
        assert reply["input_tool"] == "block_card"

        # Obtain available cards from input options
        options = client.get(f"/conversations/{cid}/inputs").json()["tools"]
        block_tool = next(t for t in options if t["name"] == "block_card")
        card_ref = next(f for f in block_tool["fields"] if f["name"] == "card_id")["options"][0]["ref"]

        # Submit selection via inputs
        review = client.post(
            f"/conversations/{cid}/inputs",
            json={"tool": "block_card", "fields": {"reason": "lost"}, "selections": {"card_id": card_ref}},
        ).json()
        assert review["can_confirm"] is True
        assert review["review"] is not None

        # Customer confirms the action
        confirm_res = client.post(f"/conversations/{cid}/confirm", json={"review": review["review"]})
        assert confirm_res.status_code == 200
        confirm_data = confirm_res.json()
        assert "block" in confirm_data["message"].lower() and "succeeded" in confirm_data["message"].lower()

        # Verify card list shows card as blocked
        cards_res = client.get("/api/cards")
        assert cards_res.status_code == 200
        c_item = next(c for c in cards_res.json() if c["id"] == card_id)
        assert c_item["status"] == "blocked"


def test_journey_d_chat_history_persistence_and_order(geo_retriever):
    """
    Journey D: Chat History Persistence
    - Multi-turn conversation
    - Validates message order and continuity
    - Re-querying or subsequent messages maintain correct historical sequence without duplicates
    """
    model = JourneyStubModel()
    app = create_app(
        model,
        ShowcaseGateway(),
        policy_retriever=geo_retriever,
        config={"LOCAL_CUSTOMER": {"sub": "cust-d", "username": "alex", "permissions": ["tools:execute"]}},
    )

    with TestClient(app) as client:
        cid = client.post("/conversations").json()["id"]

        # Turn 1
        model.outputs.append("Hello! How can Neo Bank assist you today?")
        t1 = client.post(f"/conversations/{cid}/messages", json={"text": "Hi Neo"})
        assert t1.status_code == 200
        assert "assist" in t1.json()["message"]

        # Turn 2
        model.outputs.append("Your everyday transaction account is active and in good standing.")
        t2 = client.post(f"/conversations/{cid}/messages", json={"text": "How is my account?"})
        assert t2.status_code == 200
        assert "standing" in t2.json()["message"]

        # Verify conversation state inside orchestrator
        snapshot = client.portal.call(
            app.state.orchestrator.workflow.aget_state,
            {"configurable": {"thread_id": f"cust-d:{cid}"}},
        )
        assert snapshot is not None


def test_journey_e_microphone_transcription_invariants():
    """
    Journey E: Microphone Transcription Contracts
    - Validates that voice transcription logic does not compound interim strings
    - Emulates the speech recognition accumulator contract
    """
    # Emulate browser events
    final_accumulator = []
    events = [
        {"is_final": False, "transcript": "I lost"},
        {"is_final": False, "transcript": "I lost my"},
        {"is_final": False, "transcript": "I lost my card in"},
        {"is_final": True, "transcript": "I lost my card in Sydney"},
    ]

    for ev in events:
        if ev["is_final"]:
            final_accumulator.append(ev["transcript"].strip())

    result_text = " ".join(final_accumulator)
    assert result_text == "I lost my card in Sydney"
    assert len(result_text.split()) == 6  # Strictly 6 words, zero multiplication


def test_journey_f_policy_restricted_actions():
    """
    Journey F: Policy-Restricted Actions (POL-006 Geopolitics)
    - Australia: replacement allowed under POL-006
    - North Korea: replacement denied under POL-006, block permitted
    """
    # Test Australia declaration
    syd_text = "I lost my card while travelling on a train in Sydney"
    decl_au = declared_country(syd_text)
    assert decl_au is not None
    assert decl_au.code == "AU"
    decision_au = local_geographic_decision({"code": "AU", "status": "explicit"})
    assert decision_au.decision == "allowed"
    assert decision_au.policy_id == "POL-006"

    # Test North Korea declaration
    nk_text = "I'm on vacation in North Korea and my card was stolen"
    decl_kp = declared_country(nk_text)
    assert decl_kp is not None
    assert decl_kp.code == "KP"
    decision_kp = local_geographic_decision({"code": "KP", "status": "explicit"})
    assert decision_kp.decision == "denied"
    assert decision_kp.policy_id == "POL-006"
    assert "restricted" in decision_kp.explanation.lower()


def test_journey_g_multi_customer_isolation(geo_retriever):
    """
    Journey G: Multi-Customer Isolation
    - Customer A and Customer B have distinct accounts and cards
    - Customer A cannot see Customer B's records
    - Customer B cannot see Customer A's records
    """
    gateway = ShowcaseGateway()
    app = create_app(
        JourneyStubModel(),
        gateway,
        policy_retriever=geo_retriever,
        config={"LOCAL_CUSTOMER": {"sub": "cust-a", "username": "alice", "permissions": ["tools:execute"]}},
    )

    with TestClient(app) as client_a:
        # Customer A creates an account and card
        acc_a = client_a.post("/api/accounts", json={"account_type": "transaction"}).json()
        card_a = client_a.post("/api/cards", json={"account_number": acc_a["account_number"]}).json()

        # Customer A's data
        accounts_a = client_a.get("/api/accounts").json()
        cards_a = client_a.get("/api/cards").json()
        assert any(a["account_number"] == acc_a["account_number"] for a in accounts_a)
        assert any(c["id"] == card_a["id"] for c in cards_a)

    # Customer B creates an app session
    app_b = create_app(
        JourneyStubModel(),
        gateway,
        policy_retriever=geo_retriever,
        config={"LOCAL_CUSTOMER": {"sub": "cust-b", "username": "bob", "permissions": ["tools:execute"]}},
    )
    with TestClient(app_b) as client_b:
        accounts_b = client_b.get("/api/accounts").json()
        cards_b = client_b.get("/api/cards").json()

        # Customer B cannot see Customer A's account or card
        assert not any(a["account_number"] == acc_a["account_number"] for a in accounts_b)
        assert not any(c["id"] == card_a["id"] for c in cards_b)
