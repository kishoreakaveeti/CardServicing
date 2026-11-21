"""Automated acceptance tests for FinCrime Human-in-the-Loop approval,

LangGraph interrupt/resumption, dynamic Qdrant indexing, and OPA policy evaluation.

11 Comprehensive Acceptance Tests covering Section 15:
1. Australian customer (AU domestic -> normal workflow, issued without staff approval).
2. New Zealand customer (NZ overseas -> card blocked, pauses with LangGraph interrupt & case created, staff approves, resumes, issues card).
3. New Zealand rejection (staff rejects -> workflow terminated, no replacement issued, customer sees rejection).
4. North Korea initially excluded (KP -> denied immediately, no case created, security blocking allowed).
5. Remove KP using Apply (staff removes KP from exclusions, applies release, Qdrant re-indexes, active version incremented without restart).
6. KP after policy change (new request with KP routes to FinCrime approval, staff approves, replacement card issued).
7. Customer refresh (customer refreshes state and sees issued card).
8. Live policy document update (editing approved policy content and applying updates RAG retrieval evidence).
9. Invalid policy publication rejected (invalid schema/codes fails Apply, preserves active release).
10. Security and duplicate execution prevention (duplicate approval returns error, no duplicate cards).
11. Policy changed during pending review (if country is re-excluded while case is pending, resumption revalidates and fails safely).
"""
import asyncio
from uuid import uuid4
import pytest

from card_chat.provider import StubModel
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.orchestration.persistence import DurableSqliteSaver
from card_chat.agent import AgentOrchestrator, CardServicingAgent, FinCrimeAgent
from card_chat.policy.factory import build_policy_retriever, build_policy_evaluator
from card_chat.policy.adapters.vector_stores.qdrant_store import QdrantVectorStore
from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline
from card_chat.policy.release_store import PolicyReleaseStore
from fincrime_servicing.cases import FinCrimeCaseStore
from card_servicing.persistence import SQLiteCardStore
from card_servicing.banking import BankingService, Credentials, AccountCreate


@pytest.fixture
def hitl_environment(tmp_path):
    """Fixture providing full banking, policy, vector store, and LangGraph orchestrator stack."""
    checkpoints_db = str(tmp_path / "checkpoints.db")
    cards_db = str(tmp_path / "cards.db")
    cases_db = str(tmp_path / "fincrime_cases.db")
    policy_db = str(tmp_path / "policy_store.db")

    checkpointer = DurableSqliteSaver(checkpoints_db)
    card_store = SQLiteCardStore(cards_db)
    card_store.initialize()
    banking = BankingService(card_store)

    # Initialize customer and account
    reg = banking.register(Credentials(username="alex", password="synthetic_pass_123"))
    cust = {"sub": reg["sub"], "username": "alex", "preferred_username": "alex", "accounts": []}
    acc = banking.create_account(cust, AccountCreate(account_type="savings", issue_card=True), key="acc_key_1")
    cust["accounts"] = [acc["account_number"]]

    cards = banking.cards(cust["sub"])
    assert len(cards) == 1
    initial_card = cards[0]

    # Initialize Stores
    case_store = FinCrimeCaseStore(cases_db)
    release_store = PolicyReleaseStore(policy_db)

    # Vector store & RAG
    vector_store = QdrantVectorStore()
    retriever = build_policy_retriever(
        vector_store=vector_store,
        config={"EMBEDDING_PROVIDER": "deterministic"},
        release_store=release_store,
    )
    PolicyIngestionPipeline(
        retriever._doc_source, retriever._embedder, retriever._store, audit_log_path=tmp_path / "audit.json"
    ).ingest_all()
    retriever.strict_index = True

    evaluator = build_policy_evaluator(retriever, release_store=release_store)

    model = StubModel()
    cards_agent = CardServicingAgent(model)
    fincrime_agent = FinCrimeAgent(model)

    orchestrator = AgentOrchestrator(
        model=model,
        cards_agent=cards_agent,
        fincrime_agent=fincrime_agent,
        checkpointer=checkpointer,
        policy_retriever=retriever,
        policy_evaluator=evaluator,
        check_blocked_fn=card_store.has_blocked_cards_for_account,
    )
    orchestrator.attach_fincrime_case_store(case_store)
    orchestrator.attach_policy_release_store(release_store)

    # Card gateway executing replace_card on banking service
    async def card_gateway(tool_name, tool_args, key=None, replacement_context=None):
        if tool_name == "replace_card":
            cid = tool_args.get("card_id")
            k = key or uuid4().hex
            rep_ctx = replacement_context or {}
            w_id = rep_ctx.get("workflow", uuid4().hex)
            country_code = rep_ctx.get("country_code", "NZ")
            geo_ctx = banking.declare_country(cust, w_id, {"code": country_code, "status": "explicit", "source": "customer_declaration"})
            geo_ctx["fincrime_approval"] = True
            try:
                res = banking.replace_card(
                    customer=cust,
                    card_id=cid,
                    key=k,
                    reason=tool_args.get("reason", "Replacement requested"),
                    geographic_context=geo_ctx,
                )
                return {"ok": True, "result": res.model_dump() if hasattr(res, "model_dump") else res}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": f"Unknown tool {tool_name}"}

    orchestrator.attach_card_gateway(card_gateway)

    yield {
        "banking": banking,
        "card_store": card_store,
        "customer": cust,
        "card": initial_card,
        "orchestrator": orchestrator,
        "case_store": case_store,
        "release_store": release_store,
        "retriever": retriever,
        "evaluator": evaluator,
        "card_gateway": card_gateway,
    }

    vector_store.client.close()


# -------------------------------------------------------------------------
# Test 1: Australian customer (AU domestic -> normal workflow, issued without staff approval)
# -------------------------------------------------------------------------
def test_1_australian_customer_domestic_normal_workflow(hitl_environment):
    async def scenario():
        env = hitl_environment
        orch: AgentOrchestrator = env["orchestrator"]
        banking: BankingService = env["banking"]
        cust = env["customer"]
        card = env["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:test_au_1"
        allowed_tools = {"block_card": {}, "replace_card": {}, "issue_card": {}}

        # Turn 1: Compound request with declared country AU
        history = [{"role": "user", "content": "I am in Australia. I lost my debit card. Please block it and arrange a replacement."}]
        collected = {"arguments": {"card_id": card_id, "reason": "lost card"}}
        turn1 = await orch.interpret_turn(
            history=history,
            collected=collected,
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=cust["sub"],
            country_context={"code": "AU", "status": "explicit", "source": "customer_declaration"},
        )
        assert isinstance(turn1, ProposedToolCall)
        assert turn1.name == "block_card"

        # Customer confirms card block
        block_res = banking.block_card(cust, card_id, "lost card")
        turn2 = await orch.resume_execution(
            thread_id=thread_id,
            owner=cust["sub"],
            session_id=thread_id,
            tool_name="block_card",
            result=block_res,
            ok=True,
            arguments={"card_id": card_id},
        )

        # AU is domestic: supervisor plans replace_card without FinCrime approval
        assert isinstance(turn2, ProposedToolCall)
        assert turn2.name == "replace_card"

        # Customer confirms replacement
        geo_ctx = banking.declare_country(cust, thread_id, {"code": "AU", "status": "explicit", "source": "customer_declaration"})
        rep_res = banking.replace_card(
            cust,
            card_id,
            key="rep_key_au",
            geographic_context=geo_ctx,
        )
        turn3 = await orch.resume_execution(
            thread_id=thread_id,
            owner=cust["sub"],
            session_id=thread_id,
            tool_name="replace_card",
            result=rep_res.model_dump(),
            ok=True,
            arguments={"card_id": card_id},
        )
        assert isinstance(turn3, CustomerReply)

        # Verify NO FinCrime case was created and replacement card exists
        cases = env["case_store"].list_cases()
        assert len(cases) == 0
        cards = banking.cards(cust["sub"])
        assert len(cards) == 2

    asyncio.run(scenario())


# -------------------------------------------------------------------------
# Test 2: New Zealand customer (NZ overseas -> card blocked, pauses with LangGraph interrupt & case created, staff approves, resumes, issues card)
# -------------------------------------------------------------------------
def test_2_new_zealand_customer_overseas_approval_workflow(hitl_environment):
    async def scenario():
        env = hitl_environment
        orch: AgentOrchestrator = env["orchestrator"]
        banking: BankingService = env["banking"]
        cust = env["customer"]
        card = env["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:test_nz_1"
        allowed_tools = {"block_card": {}, "replace_card": {}, "issue_card": {}}

        # Turn 1: Compound request with declared country NZ
        history = [{"role": "user", "content": "I am currently in New Zealand. I lost my card. Please block it and arrange a replacement."}]
        collected = {"arguments": {"card_id": card_id, "reason": "lost card"}}
        turn1 = await orch.interpret_turn(
            history=history,
            collected=collected,
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=cust["sub"],
            country_context={"code": "NZ", "status": "explicit", "source": "customer_declaration"},
        )
        assert isinstance(turn1, ProposedToolCall)
        assert turn1.name == "block_card"

        # Customer confirms block
        block_res = banking.block_card(cust, card_id, "lost card")
        turn2 = await orch.resume_execution(
            thread_id=thread_id,
            owner=cust["sub"],
            session_id=thread_id,
            tool_name="block_card",
            result=block_res,
            ok=True,
            arguments={"card_id": card_id},
        )

        # Replacement requires FinCrime approval: workflow pauses at interrupt and returns customer status message
        assert isinstance(turn2, CustomerReply)
        assert "pending fincrime" in turn2.text.lower() or "case" in turn2.text.lower()

        # Check FinCrime case was created
        cases = env["case_store"].list_cases(status="PENDING")
        assert len(cases) == 1
        case = cases[0]
        assert case.current_country == "NZ"
        assert case.status == "PENDING"

        # Staff approves case
        res = await orch.resume_fincrime_case(
            case_id=case.case_id,
            decision="approved",
            reason="Verified overseas travel to Auckland",
            analyst_username="fincrime.analyst",
        )
        assert res["ok"] is True
        assert res["status"] == "COMPLETED"

        # Verify replacement card issued in Cards store
        updated_case = env["case_store"].get_case(case.case_id)
        assert updated_case.status == "COMPLETED"
        assert updated_case.decided_by == "fincrime.analyst"
        cards = banking.cards(cust["sub"])
        assert len(cards) == 2

    asyncio.run(scenario())


# -------------------------------------------------------------------------
# Test 3: New Zealand rejection (staff rejects -> workflow terminated, no replacement issued, customer sees rejection)
# -------------------------------------------------------------------------
def test_3_new_zealand_rejection_workflow(hitl_environment):
    async def scenario():
        env = hitl_environment
        orch: AgentOrchestrator = env["orchestrator"]
        banking: BankingService = env["banking"]
        cust = env["customer"]
        card = env["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:test_nz_reject"
        allowed_tools = {"block_card": {}, "replace_card": {}, "issue_card": {}}

        history = [{"role": "user", "content": "I am in New Zealand. Block and replace my card."}]
        collected = {"arguments": {"card_id": card_id, "reason": "lost card"}}
        turn1 = await orch.interpret_turn(
            history=history,
            collected=collected,
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=cust["sub"],
            country_context={"code": "NZ", "status": "explicit"},
        )
        block_res = banking.block_card(cust, card_id, "lost card")
        turn2 = await orch.resume_execution(
            thread_id=thread_id,
            owner=cust["sub"],
            session_id=thread_id,
            tool_name="block_card",
            result=block_res,
            ok=True,
            arguments={"card_id": card_id},
        )
        cases = env["case_store"].list_cases(status="PENDING")
        assert len(cases) == 1
        case = cases[0]

        # Staff rejects case
        res = await orch.resume_fincrime_case(
            case_id=case.case_id,
            decision="rejected",
            reason="Suspected credential compromise",
            analyst_username="fincrime.analyst",
        )
        assert res["ok"] is True
        assert res["status"] == "REJECTED"

        updated_case = env["case_store"].get_case(case.case_id)
        assert updated_case.status == "REJECTED"

        # Verify NO replacement card issued
        cards = banking.cards(cust["sub"])
        assert len(cards) == 1
        assert cards[0].status == "blocked"

    asyncio.run(scenario())


# -------------------------------------------------------------------------
# Test 4: North Korea initially excluded (KP -> denied immediately, no case created, security blocking allowed)
# -------------------------------------------------------------------------
def test_4_north_korea_excluded_immediate_denial(hitl_environment):
    async def scenario():
        env = hitl_environment
        orch: AgentOrchestrator = env["orchestrator"]
        banking: BankingService = env["banking"]
        cust = env["customer"]
        card = env["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:test_kp_1"
        allowed_tools = {"block_card": {}, "replace_card": {}, "issue_card": {}}

        # User in KP requests block and replacement
        history = [{"role": "user", "content": "I am currently in North Korea. I lost my debit card. Please block it and arrange a replacement."}]
        collected = {"arguments": {"card_id": card_id, "reason": "lost card"}}
        turn1 = await orch.interpret_turn(
            history=history,
            collected=collected,
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=cust["sub"],
            country_context={"code": "KP", "status": "explicit"},
        )

        # Security card blocking is still permitted!
        assert isinstance(turn1, ProposedToolCall)
        assert turn1.name == "block_card"

        # Customer confirms block
        block_res = banking.block_card(cust, card_id, "lost card")
        turn2 = await orch.resume_execution(
            thread_id=thread_id,
            owner=cust["sub"],
            session_id=thread_id,
            tool_name="block_card",
            result=block_res,
            ok=True,
            arguments={"card_id": card_id},
        )

        # Replacement is hard-denied: supervisor informs customer
        assert isinstance(turn2, CustomerReply)
        assert "not permitted" in turn2.text.lower() or "prohibited" in turn2.text.lower() or "pol-006" in turn2.text.lower() or "restricted" in turn2.text.lower()

        # Hard denial must NOT create a FinCrime case
        cases = env["case_store"].list_cases()
        assert len(cases) == 0

        # No replacement card issued
        cards = banking.cards(cust["sub"])
        assert len(cards) == 1
        assert cards[0].status == "blocked"

    asyncio.run(scenario())


# -------------------------------------------------------------------------
# Test 5: Remove KP using Apply (staff removes KP from exclusions, applies release, Qdrant re-indexes, active version incremented without restart)
# -------------------------------------------------------------------------
def test_5_dynamic_policy_apply_removes_kp_exclusion(hitl_environment):
    env = hitl_environment
    release_store: PolicyReleaseStore = env["release_store"]
    retriever = env["retriever"]

    initial_release = release_store.get_active_release("POL-006")
    assert "KP" in initial_release["rules_snapshot"].get("excluded_country_codes", [])

    # Update rules to remove KP
    updated_rules = {
        "policy_id": "POL-006",
        "home_country_code": "AU",
        "excluded_country_codes": [],
        "overseas_replacement_requires_fincrime_approval": True,
        "domestic_replacement_requires_fincrime_approval": False,
        "unknown_country_action": "REQUIRE_CLARIFICATION",
        "security_card_blocking_allowed": True,
    }
    updated_content = """# POL-006: Geographic Replacement Restrictions

Domestic replacements (AU) proceed with automated eligibility.
Overseas replacements require FinCrime human approval prior to card issuance.
No countries are currently excluded under demonstration configuration.
"""

    new_release = release_store.create_release(
        policy_id="POL-006",
        content=updated_content,
        rules=updated_rules,
        activated_by="fincrime.admin",
        change_summary="Removed KP from excluded countries list live",
        retriever=retriever,
    )

    assert new_release["version"] != initial_release["version"]
    assert "KP" not in new_release["rules_snapshot"].get("excluded_country_codes", [])

    # Verify active rules in store
    active = release_store.get_active_release("POL-006")
    assert active["release_id"] == new_release["release_id"]
    assert active["rules_snapshot"].get("excluded_country_codes") == []


# -------------------------------------------------------------------------
# Test 6: KP after policy change (new request with KP routes to FinCrime approval, staff approves, replacement card issued)
# -------------------------------------------------------------------------
def test_6_kp_after_policy_change_routes_to_approval(hitl_environment):
    env = hitl_environment
    release_store: PolicyReleaseStore = env["release_store"]
    retriever = env["retriever"]

    # Remove KP from exclusions
    release_store.create_release(
        policy_id="POL-006",
        content="# POL-006: Geographic Replacement Restrictions\nAll overseas countries require FinCrime staff approval.",
        rules={
            "policy_id": "POL-006",
            "home_country_code": "AU",
            "excluded_country_codes": [],
            "overseas_replacement_requires_fincrime_approval": True,
            "domestic_replacement_requires_fincrime_approval": False,
            "unknown_country_action": "REQUIRE_CLARIFICATION",
            "security_card_blocking_allowed": True,
        },
        activated_by="fincrime.admin",
        change_summary="Removed KP exclusion",
        retriever=retriever,
    )

    async def scenario():
        orch: AgentOrchestrator = env["orchestrator"]
        banking: BankingService = env["banking"]
        cust = env["customer"]
        card = env["card"]
        card_id = str(card.id)

        thread_id = f"{cust['sub']}:test_kp_after_policy"
        allowed_tools = {"block_card": {}, "replace_card": {}, "issue_card": {}}

        history = [{"role": "user", "content": "I am in North Korea. I lost my debit card. Please block and replace my card."}]
        collected = {"arguments": {"card_id": card_id, "reason": "lost card"}}
        turn1 = await orch.interpret_turn(
            history=history,
            collected=collected,
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=cust["sub"],
            country_context={"code": "KP", "status": "explicit"},
        )
        block_res = banking.block_card(cust, card_id, "lost card")
        turn2 = await orch.resume_execution(
            thread_id=thread_id,
            owner=cust["sub"],
            session_id=thread_id,
            tool_name="block_card",
            result=block_res,
            ok=True,
            arguments={"card_id": card_id},
        )

        # Because KP is no longer excluded, it is overseas -> creates FinCrime case!
        assert isinstance(turn2, CustomerReply)
        cases = env["case_store"].list_cases(status="PENDING")
        assert len(cases) == 1
        case = cases[0]
        assert case.current_country == "KP"

        # Staff approves
        res = await orch.resume_fincrime_case(
            case_id=case.case_id,
            decision="approved",
            reason="Approved under updated policy POL-006-R2",
            analyst_username="fincrime.analyst",
        )
        assert res["ok"] is True
        assert res["status"] == "COMPLETED"

        cards = banking.cards(cust["sub"])
        assert len(cards) == 2

    asyncio.run(scenario())


# -------------------------------------------------------------------------
# Test 7: Customer refresh (customer refreshes state and sees issued card)
# -------------------------------------------------------------------------
def test_7_customer_refresh_sees_completed_replacement(hitl_environment):
    env = hitl_environment
    case_store: FinCrimeCaseStore = env["case_store"]
    cust = env["customer"]
    card = env["card"]

    # Create and complete a case
    case = case_store.create_case(
        customer_id=cust["sub"],
        card_id=str(card.id),
        action="replace_card",
        current_country="NZ",
        home_country="AU",
        reasons=["Overseas replacement"],
    )
    case_store.approve_case(case.case_id, "fincrime.analyst", "Verified")
    case_store.mark_processing(case.case_id)
    case_store.mark_completed(case.case_id, "card_new_123")

    # Customer queries replacement status
    cases = case_store.list_cases(customer_id=cust["sub"])
    assert len(cases) >= 1
    completed_case = cases[0]
    assert completed_case.status == "COMPLETED"
    assert completed_case.replacement_card_id == "card_new_123"


def test_confirmed_chat_case_executes_without_graph_tool_proposal(hitl_environment):
    """A case created by chat confirmation is the immutable approved request."""
    async def scenario():
        env = hitl_environment
        banking = env["banking"]
        customer = env["customer"]
        card = env["card"]
        case_store = env["case_store"]
        orchestrator = env["orchestrator"]

        banking.block_card(customer, str(card.id), "lost")
        binding = banking.declare_country(
            customer,
            "chat-confirmed-workflow",
            {"code": "AU", "status": "explicit", "source": "customer_declaration"},
        )
        case = case_store.create_case(
            customer_id=customer["sub"],
            customer_username=customer["username"],
            account_number=card.account_number,
            card_id=str(card.id),
            card_last4=str(card.id)[-4:],
            declared_country="AU",
            is_overseas=False,
            policy_id="POL-006",
            policy_version="1.0.0",
            rules_version="1.0.0",
            reason_for_escalation="Synthetic transition review",
            rag_evidence=[],
            evaluation_result={"geographic_binding": binding},
            thread_id=f'{customer["sub"]}:chat-confirmed-workflow',
        )

        result = await orchestrator.resume_fincrime_case(
            case_id=case.case_id,
            decision="approved",
            reason="Approved confirmed request",
            analyst_username="fincrime.analyst",
        )

        assert result["ok"] is True
        assert result["status"] == "COMPLETED"
        assert case_store.get_case(case.case_id).replacement_card_id

    asyncio.run(scenario())


# -------------------------------------------------------------------------
# Test 8: Live policy document update (editing approved policy content and applying updates RAG retrieval evidence)
# -------------------------------------------------------------------------
def test_8_live_policy_document_update_rag_evidence(hitl_environment):
    env = hitl_environment
    release_store: PolicyReleaseStore = env["release_store"]
    retriever = env["retriever"]

    custom_text = (
        "# POL-006: Geographic Replacement Restrictions\n\n"
        "Special Emergency Travel Protocol: Diplomatic and emergency assistance replacements "
        "require Level-3 FinCrime Supervisor authorization."
    )
    release_store.create_release(
        policy_id="POL-006",
        content=custom_text,
        rules={
            "policy_id": "POL-006",
            "home_country_code": "AU",
            "excluded_country_codes": ["KP"],
            "overseas_replacement_requires_fincrime_approval": True,
            "domestic_replacement_requires_fincrime_approval": False,
        },
        activated_by="fincrime.admin",
        change_summary="Added Emergency Travel Protocol",
        retriever=retriever,
    )

    # Query retriever
    results = retriever.retrieve("replace_card", {}, query="Emergency Travel Protocol Level-3 Supervisor")
    assert any(r.policy_id == "POL-006" for r in results)
    active = release_store.get_active_release("POL-006")
    assert "Emergency Travel Protocol" in active["markdown"]


# -------------------------------------------------------------------------
# Test 9: Invalid policy publication rejected (invalid schema/codes fails Apply, preserves active release)
# -------------------------------------------------------------------------
def test_9_invalid_policy_publication_rejected(hitl_environment):
    env = hitl_environment
    release_store: PolicyReleaseStore = env["release_store"]
    initial_release = release_store.get_active_release("POL-006")

    # Invalid country code (3 letters)
    invalid_rules = {
        "policy_id": "POL-006",
        "home_country_code": "AU",
        "excluded_country_codes": ["INVALID_CODE"],
        "overseas_replacement_requires_fincrime_approval": True,
    }

    with pytest.raises(Exception):
        release_store.create_release(
            policy_id="POL-006",
            content="# Invalid Policy",
            rules=invalid_rules,
            activated_by="fincrime.admin",
            change_summary="Bad release",
        )

    # Active release is unchanged
    current_release = release_store.get_active_release("POL-006")
    assert current_release["release_id"] == initial_release["release_id"]


# -------------------------------------------------------------------------
# Test 10: Security and duplicate execution prevention (duplicate approval returns error, no duplicate cards)
# -------------------------------------------------------------------------
def test_10_security_duplicate_execution_prevention(hitl_environment):
    async def scenario():
        env = hitl_environment
        orch: AgentOrchestrator = env["orchestrator"]
        case_store: FinCrimeCaseStore = env["case_store"]
        cust = env["customer"]
        card = env["card"]

        # Create case
        case = case_store.create_case(
            customer_id=cust["sub"],
            card_id=str(card.id),
            action="replace_card",
            current_country="NZ",
            home_country="AU",
            conversation_id=f"{cust['sub']}:dup_test",
        )

        # First approval
        case_store.approve_case(case.case_id, "fincrime.analyst", "First approval")

        # Duplicate approval must fail
        with pytest.raises(ValueError):
            case_store.approve_case(case.case_id, "fincrime.analyst", "Duplicate approval attempt")

        with pytest.raises(ValueError):
            await orch.resume_fincrime_case(
                case_id=case.case_id,
                decision="approved",
                reason="Duplicate resume attempt",
                analyst_username="fincrime.analyst",
            )

    asyncio.run(scenario())


# -------------------------------------------------------------------------
# Test 11: Policy changed during pending review (if country is re-excluded while case is pending, resumption revalidates and fails safely)
# -------------------------------------------------------------------------
def test_11_policy_changed_during_pending_review_fails_safely(hitl_environment):
    async def scenario():
        env = hitl_environment
        orch: AgentOrchestrator = env["orchestrator"]
        banking: BankingService = env["banking"]
        cust = env["customer"]
        card = env["card"]
        card_id = str(card.id)
        release_store: PolicyReleaseStore = env["release_store"]
        retriever = env["retriever"]

        thread_id = f"{cust['sub']}:test_policy_shift"
        allowed_tools = {"block_card": {}, "replace_card": {}, "issue_card": {}}

        # Customer is in NZ. Initial policy allows NZ under FinCrime approval.
        history = [{"role": "user", "content": "I am in New Zealand. Block and replace my card."}]
        collected = {"arguments": {"card_id": card_id, "reason": "lost card"}}
        turn1 = await orch.interpret_turn(
            history=history,
            collected=collected,
            allowed_tools=allowed_tools,
            thread_id=thread_id,
            owner=cust["sub"],
            country_context={"code": "NZ", "status": "explicit"},
        )
        block_res = banking.block_card(cust, card_id, "lost card")
        await orch.resume_execution(
            thread_id=thread_id,
            owner=cust["sub"],
            session_id=thread_id,
            tool_name="block_card",
            result=block_res,
            ok=True,
            arguments={"card_id": card_id},
        )
        cases = env["case_store"].list_cases(status="PENDING")
        assert len(cases) == 1
        case = cases[0]

        # While review is pending, admin activates a new policy release EXCLUDING NZ!
        release_store.create_release(
            policy_id="POL-006",
            content="# POL-006: Geographic Replacement Restrictions\nNZ is now excluded.",
            rules={
                "policy_id": "POL-006",
                "home_country_code": "AU",
                "excluded_country_codes": ["KP", "NZ"],
                "overseas_replacement_requires_fincrime_approval": True,
                "domestic_replacement_requires_fincrime_approval": False,
            },
            activated_by="fincrime.admin",
            change_summary="NZ added to exclusion list",
            retriever=retriever,
        )

        # Analyst attempts to approve case
        res = await orch.resume_fincrime_case(
            case_id=case.case_id,
            decision="approved",
            reason="Analyst approved without noticing policy change",
            analyst_username="fincrime.analyst",
        )

        # Workflow revalidates active policy upon resumption, catches that NZ is now excluded, fails safely!
        assert res["ok"] is False
        assert res["status"] == "FAILED"
        assert "policy changed" in res["error"].lower() or "no longer permitted" in res["error"].lower()

        # No replacement card is issued
        cards = banking.cards(cust["sub"])
        assert len(cards) == 1
        assert cards[0].status == "blocked"

    asyncio.run(scenario())
