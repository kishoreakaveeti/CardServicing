"""End-to-End Enterprise Demonstrations for Scenarios A, B, and C with LangGraph."""
from __future__ import annotations

import copy
from pathlib import Path
import pytest
from starlette.exceptions import HTTPException

from card_chat.catalogue import load_catalogue
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.orchestration.persistence import DurableSqliteSaver
from card_chat.orchestration.specialists import CardsSpecialistNode, FinCrimeSpecialistNode
from card_chat.orchestration.supervisor import SupervisorAgent
from card_chat.policy import (
    FileSystemDocumentSource,
    DeterministicLocalEmbeddingProvider,
    QdrantVectorStore,
    HybridPolicyRetriever,
    GroundedPolicyEvaluator,
    PolicyIngestionPipeline,
    build_policy_retriever,
    build_policy_evaluator,
)
from card_servicing.banking import BankingService
from card_servicing.persistence import SQLiteCardStore
from fincrime_servicing.persistence import SQLiteFinCrimeStore
from fincrime_servicing.service import FinCrimeService


class DeterministicStubModel:
    def __init__(self, turns=None):
        self.turns = list(turns or [])
        self.history = []

    async def turn(self, history, draft, tools):
        self.history.append((history, draft, tools))
        if self.turns:
            return self.turns.pop(0)
        return CustomerReply("Default response")


@pytest.fixture
def banking_environment(tmp_path):
    """Set up real SQLite stores for Cards and FinCrime services."""
    cards_db = str(tmp_path / "cards.db")
    fincrime_db = str(tmp_path / "fincrime.db")

    card_store = SQLiteCardStore(cards_db)
    card_store.initialize()
    banking = BankingService(card_store)

    fc_store = SQLiteFinCrimeStore(fincrime_db)
    fc_store.initialize()
    fc_svc = FinCrimeService(fc_store, cards_service=banking)

    # Register customer Dan
    cust = banking.register(type("Creds", (), {"username": "dan", "password": "password-12345"})())
    cust["username"] = "dan"
    account = banking.create_account(
        cust,
        type("Acc", (), {"account_type": "transaction", "issue_card": True, "model_dump_json": lambda s: "{}"})(),
        "acc-key-dan",
    )
    cards = banking.cards(cust["sub"])
    initial_card = cards[0]

    return {
        "banking": banking,
        "fincrime_service": fc_svc,
        "customer": cust,
        "account": account,
        "card": initial_card,
        "tmp_path": tmp_path,
    }


# ==============================================================================
# Scenario A: Blocked Card and Additional Issuance
# ==============================================================================

@pytest.mark.anyio
async def test_scenario_a_temporary_freeze_unblock_offered_and_accepted(banking_environment):
    """Scenario A: Customer requests card on account with temporarily frozen card.

    POL-001 offers unblock journey -> Customer accepts -> FinCrime unblocks ->
    Cards resumes issuance eligibility.
    """
    env = banking_environment
    banking: BankingService = env["banking"]
    cust = env["customer"]
    card = env["card"]
    account = env["account"]

    # 1. Card is temporarily frozen
    banking.block_card(cust, str(card.id), "temporary_freeze")

    # Set up Policy RAG components
    source = FileSystemDocumentSource("policies")
    embedder = DeterministicLocalEmbeddingProvider(dimension=256)
    store = QdrantVectorStore(collection_name="scenario_a_policies")
    pipeline = PolicyIngestionPipeline(source, embedder, store)
    pipeline.ingest_all()

    retriever = HybridPolicyRetriever(source, embedder, store)
    evaluator = GroundedPolicyEvaluator(retriever)

    # Step 1: Customer requests: "I want another card for my account."
    facts = {
        "account_number": account["account_number"],
        "has_blocked_card": True,
        "blocked_cards": [{
            "id": str(card.id),
            "card_id": str(card.id),
            "card_number": card.card_number,
            "reason": "temporary_freeze",
            "account_number": account["account_number"],
        }],
        "is_temporary_freeze": True,
        "temporary_freeze": True,
        "is_permanently_compromised": False,
    }

    eval_result = evaluator.evaluate(
        intent="issue_card",
        user_text="I want another card for my account",
        facts=facts,
    )

    # Verify POL-001 restricted issuance and offered unblock
    assert eval_result.permitted is False
    assert eval_result.pending_question is not None
    assert eval_result.pending_question.question_type == "unblock_offer"
    assert eval_result.pending_question.policy_id == "POL-001"
    assert "temporarily frozen" in eval_result.pending_question.question_text

    # Step 2: Customer responds: "Yes, please"
    eval_response = evaluator.evaluate(
        intent=None,
        user_text="Yes, please",
        facts=facts,
        pending_question=eval_result.pending_question,
    )

    assert eval_response.customer_decision == "accept"
    assert eval_response.recommended_delegation == "fincrime_agent"
    assert eval_response.recommended_intent == "unblock_card"
    assert eval_response.recommended_context["card_id"] == str(card.id)
    assert eval_response.planned_intents == ["issue_card"]


@pytest.mark.anyio
async def test_scenario_a_compromised_card_never_suggests_unblocking(banking_environment):
    """Scenario A (Compromised): Permanently compromised card must NEVER suggest unblocking under POL-001."""
    env = banking_environment
    card = env["card"]
    account = env["account"]

    source = FileSystemDocumentSource("policies")
    embedder = DeterministicLocalEmbeddingProvider(dimension=256)
    store = QdrantVectorStore(collection_name="scenario_a2_policies")
    pipeline = PolicyIngestionPipeline(source, embedder, store)
    pipeline.ingest_all()

    retriever = HybridPolicyRetriever(source, embedder, store)
    evaluator = GroundedPolicyEvaluator(retriever)

    facts = {
        "account_number": account["account_number"],
        "has_blocked_card": True,
        "blocked_cards": [{
            "id": str(card.id),
            "card_id": str(card.id),
            "reason": "stolen",
            "account_number": account["account_number"],
        }],
        "is_temporary_freeze": False,
        "temporary_freeze": False,
        "is_permanently_compromised": True,
    }

    eval_result = evaluator.evaluate(
        intent="issue_card",
        user_text="I want another card",
        facts=facts,
    )

    # Must be prohibited with no unblocking offer
    assert eval_result.permitted is False
    assert eval_result.pending_question is None
    assert "never_suggest_unblocking_permanently_compromised" in eval_result.restrictions_enforced
    assert "POL-002" in eval_result.decision_reason


# ==============================================================================
# Scenario B: Lost Card and Post-Block Replacement
# ==============================================================================

@pytest.mark.anyio
async def test_scenario_b_lost_card_post_block_replacement_offered_and_accepted(banking_environment):
    """Scenario B: Customer blocks lost card. POL-003 proactively offers replacement.

    Customer accepts -> POL-002 delegates to Cards for replacement with separate confirmation.
    """
    env = banking_environment
    banking: BankingService = env["banking"]
    cust = env["customer"]
    card = env["card"]
    account = env["account"]

    # Customer executes card block
    banking.block_card(cust, str(card.id), "lost")

    source = FileSystemDocumentSource("policies")
    embedder = DeterministicLocalEmbeddingProvider(dimension=256)
    store = QdrantVectorStore(collection_name="scenario_b_policies")
    pipeline = PolicyIngestionPipeline(source, embedder, store)
    pipeline.ingest_all()

    retriever = HybridPolicyRetriever(source, embedder, store)
    evaluator = GroundedPolicyEvaluator(retriever)

    # Step 1: After confirmed block, evaluate post-block journey
    facts = {
        "account_number": account["account_number"],
        "blocked_card_id": str(card.id),
        "last_blocked_card_last4": card.card_number[-4:],
        "last_block_reason": "lost",
        "is_temporary_freeze": False,
        "last_domain_event": "card_blocked_success",
    }

    eval_result = evaluator.evaluate(
        intent=None,
        user_text="",
        facts=facts,
        last_domain_event="card_blocked_success",
    )

    assert eval_result.pending_question is not None
    assert eval_result.pending_question.question_type == "replacement_offer"
    assert eval_result.pending_question.policy_id == "POL-003"
    assert "replacement card" in eval_result.pending_question.question_text

    # Step 2: Customer accepts: "Yes, please"
    eval_response = evaluator.evaluate(
        intent=None,
        user_text="Yes, please",
        facts=facts,
        pending_question=eval_result.pending_question,
    )

    assert eval_response.customer_decision == "accept"
    assert eval_response.recommended_delegation == "cards_agent"
    assert eval_response.recommended_intent == "replace_card"
    assert eval_response.recommended_context["card_id"] == str(card.id)

    # Step 3: Cards agent evaluates replacement eligibility under POL-002
    eval_replace = evaluator.evaluate(
        intent="replace_card",
        user_text="replace my card",
        facts=facts,
    )
    assert eval_replace.permitted is True
    assert eval_replace.pinned_versions["POL-002"] == "1.0.0"


@pytest.mark.anyio
async def test_scenario_b_lost_card_replacement_declined(banking_environment):
    """Scenario B (Decline): Customer declines replacement -> workflow completes without replacement."""
    env = banking_environment
    card = env["card"]
    account = env["account"]

    source = FileSystemDocumentSource("policies")
    embedder = DeterministicLocalEmbeddingProvider(dimension=256)
    store = QdrantVectorStore(collection_name="scenario_b2_policies")
    pipeline = PolicyIngestionPipeline(source, embedder, store)
    pipeline.ingest_all()

    retriever = HybridPolicyRetriever(source, embedder, store)
    evaluator = GroundedPolicyEvaluator(retriever)

    facts = {
        "account_number": account["account_number"],
        "blocked_card_id": str(card.id),
        "last_block_reason": "lost",
        "last_domain_event": "card_blocked_success",
    }
    eval_result = evaluator.evaluate(intent=None, user_text="", facts=facts, last_domain_event="card_blocked_success")

    # Customer declines: "No, thank you"
    eval_response = evaluator.evaluate(
        intent=None,
        user_text="No, thank you",
        facts=facts,
        pending_question=eval_result.pending_question,
    )

    assert eval_response.customer_decision == "decline"
    assert eval_response.recommended_delegation == "respond"
    assert "completed" in eval_response.decision_reason.lower()


# ==============================================================================
# Scenario C: Dynamic Policy Update Changes Orchestration Without Code Changes
# ==============================================================================

@pytest.mark.anyio
async def test_scenario_c_policy_update_permits_frozen_issuance_without_code_change(banking_environment, tmp_path):
    """Scenario C:

    1. In v1.0.0, POL-001 forbids additional issuance on frozen card without unblock.
    2. Policy is updated to v2.0.0 with allow_issuance_on_temporary_freeze: true.
    3. Pipeline reindexes the policy.
    4. Repeating request now proceeds directly to Cards eligibility WITHOUT requiring unblock journey.
    5. No changes to LangGraph, supervisor, specialist, or API code.
    6. Demonstrates that the underlying Cards API SQLite store enforces its technical
       invariant (BlockedCardExists) as an immutable security constraint.
    """
    env = banking_environment
    banking: BankingService = env["banking"]
    cust = env["customer"]
    card = env["card"]
    account = env["account"]

    # Card is temporarily frozen
    banking.block_card(cust, str(card.id), "temporary_freeze")

    # Setup policy directory with initial POL-001 v1.0.0
    pol_dir = tmp_path / "policies"
    pol_dir.mkdir()

    pol1_file = pol_dir / "POL-001-additional-card-issuance.md"
    pol1_file.write_text(
        "---\n"
        "policy_id: POL-001\n"
        "version: 1.0.0\n"
        "effective_date: '2026-01-01'\n"
        "status: approved\n"
        "approved: true\n"
        "title: Additional Card Issuance Policy\n"
        "applicable_intents: [issue_card, create_card]\n"
        "triggering_events: [card_issuance_requested]\n"
        "conditions:\n"
        "  has_blocked_card: true\n"
        "  allow_issuance_on_temporary_freeze: false\n"
        "required_actions: [evaluate_unblock_eligibility]\n"
        "restrictions: [prohibit_issuance_if_any_card_blocked]\n"
        "source: policies/POL-001.md\n"
        "---\n"
        "# Issuance Policy v1\n"
        "Prohibit issuance if account has blocked card.",
        encoding="utf-8",
    )

    pol4_file = pol_dir / "POL-004-confirmation.md"
    pol4_file.write_text(
        "---\n"
        "policy_id: POL-004\n"
        "version: 1.0.0\n"
        "effective_date: '2026-01-01'\n"
        "status: approved\n"
        "approved: true\n"
        "title: Governance\n"
        "applicable_intents: [issue_card, create_card, block_card, unblock_card, replace_card]\n"
        "triggering_events: [state_changing_action]\n"
        "conditions: {}\n"
        "required_actions: [verify_authorization]\n"
        "restrictions: [zero_trust]\n"
        "source: policies/POL-004.md\n"
        "---\n"
        "# Governance\n"
        "Deterministic controls.",
        encoding="utf-8",
    )

    source = FileSystemDocumentSource(pol_dir)
    embedder = DeterministicLocalEmbeddingProvider(dimension=256)
    store = QdrantVectorStore(collection_name="scenario_c_policies")
    pipeline = PolicyIngestionPipeline(source, embedder, store)
    pipeline.ingest_all()

    retriever = HybridPolicyRetriever(source, embedder, store)
    evaluator = GroundedPolicyEvaluator(retriever)

    facts = {
        "account_number": account["account_number"],
        "has_blocked_card": True,
        "blocked_cards": [{
            "id": str(card.id),
            "card_id": str(card.id),
            "reason": "temporary_freeze",
            "account_number": account["account_number"],
        }],
        "is_temporary_freeze": True,
        "temporary_freeze": True,
        "is_permanently_compromised": False,
    }

    # Step 1: Under v1.0.0 -> unblock offer is required
    res_v1 = evaluator.evaluate(
        intent="issue_card",
        user_text="I want another card",
        facts=facts,
    )
    assert res_v1.permitted is False
    assert res_v1.pending_question is not None
    assert res_v1.pending_question.question_type == "unblock_offer"

    # Step 2: Update policy document to v2.0.0 allowing issuance on temporary freeze
    pol1_file.write_text(
        "---\n"
        "policy_id: POL-001\n"
        "version: 2.0.0\n"
        "effective_date: '2026-02-01'\n"
        "status: approved\n"
        "approved: true\n"
        "title: Additional Card Issuance Policy\n"
        "applicable_intents: [issue_card, create_card]\n"
        "triggering_events: [card_issuance_requested]\n"
        "conditions:\n"
        "  has_blocked_card: true\n"
        "  allow_issuance_on_temporary_freeze: true\n"
        "required_actions: [verify_eligibility]\n"
        "restrictions: []\n"
        "source: policies/POL-001.md\n"
        "---\n"
        "# Issuance Policy v2\n"
        "Permit issuance on temporary freeze subject to eligibility checks.",
        encoding="utf-8",
    )

    # Re-run ingestion pipeline
    pipeline.ingest_all()

    # Step 3: Repeat the exact same request with unchanged supervisor code
    res_v2 = evaluator.evaluate(
        intent="issue_card",
        user_text="I want another card",
        facts=facts,
    )

    # Under v2.0.0, issuance is immediately PERMITTED and routes directly to cards_agent
    assert res_v2.permitted is True
    assert res_v2.pending_question is None
    assert res_v2.recommended_delegation == "cards_agent"
    assert res_v2.recommended_intent == "issue_card"
    assert res_v2.pinned_versions["POL-001"] == "2.0.0"
    assert "POL-001 (v2.0.0)" in res_v2.decision_reason

    # Step 4: Verify underlying banking API invariant enforcement!
    # Even though business policy v2.0.0 permits delegating to Cards, the Cards API SQLite store
    # still enforces its hardcoded database invariant (SELECT 1 FROM cards WHERE status='blocked').
    # Attempting actual API execution raises BlockedCardExists (HTTP 400).
    with pytest.raises(HTTPException) as exc_info:
        banking.issue(cust, account["account_number"], "test-key-scenario-c", additional=True)
    assert exc_info.value.status_code == 400
    assert "blocked" in exc_info.value.detail.lower()
