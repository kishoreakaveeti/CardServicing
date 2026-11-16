import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse

from card_chat.config import settings
from card_chat.gateway import connect
from card_chat.provider import make_model
from card_chat.agent import CardServicingAgent, FinCrimeAgent, AgentOrchestrator
from card_chat.mcp_server import get_agent_tools_policy
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.contracts import Message, Confirmation, Reply, Operation
from card_chat.durable import Journal
from card_chat.security import protect, principal, delegation, trace
import json
from dataclasses import asdict
import hashlib
import logging
from card_chat.contracts import StructuredInput, InputOptions
from fastapi.exceptions import RequestValidationError
from card_chat.model_context import (PRIVATE_PASTE, PRIVATE_NOTICE, REFERENCE, sanitise, build_context,
                                     tools_for_model, ambiguous_old_reference)
from card_chat.private_inputs import eligible, bind, resolve, authorise, form_options, mask
from card_chat.pii.gateway import PIIGateway
from card_chat.pii.vault import InMemoryTokenVault

logger = logging.getLogger(__name__)


@dataclass
class Conversation:
    history: list = field(default_factory=list)
    draft: dict = field(default_factory=dict)
    proposal: ProposedToolCall | None = None
    review: str | None = None
    submission: dict | None = None
    bindings: dict = field(default_factory=dict)
    compound_approval: dict | None = None
    usage: dict = field(default_factory=lambda: {key: 0 for key in ('model_calls', 'input_tokens',
        'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'completed_actions')})
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def create_app(model=None, gateway=None, *, config=None, policy_retriever=None):
    config = settings() if config is None else config
    journal = Journal(config.get('AGENT_DB', ':memory:'))
    tasks = set()
    worker_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if model is None and gateway is None and not config.get('OIDC_ISSUER') and config.get('ALLOW_INSECURE_LOCAL') != '1':
            raise RuntimeError('Configure OIDC before starting Agent; isolated local tests must opt into ALLOW_INSECURE_LOCAL=1')
        app.state.token_vault = InMemoryTokenVault()
        app.state.pii_gateway = PIIGateway(vault=app.state.token_vault)
        from card_chat.pii.gateway import set_default_pii_gateway
        set_default_pii_gateway(app.state.pii_gateway)
        app.state.model = model or make_model(config)
        cards_agent = CardServicingAgent(app.state.model)
        local_fincrime = FinCrimeAgent(app.state.model)
        fincrime_agent_url = config.get('FINCRIME_AGENT_URL')
        if fincrime_agent_url:
            from card_chat.fincrime_agent import RemoteFinCrimeAgent

            async def get_fincrime_service_headers():
                sec = getattr(app.state, 'security', None)
                if sec and hasattr(sec, 'headers'):
                    return await sec.headers()
                return {}

            fincrime_agent = RemoteFinCrimeAgent(
                fincrime_agent_url,
                fallback_agent=local_fincrime,
                headers_fn=get_fincrime_service_headers,
            )
        else:
            fincrime_agent = local_fincrime
        from card_chat.orchestration.persistence import DurableSqliteSaver
        from card_chat.policy.factory import build_runtime_policy_services
        checkpointer = DurableSqliteSaver(config.get('AGENT_DB', ':memory:'))
        retriever, policy_evaluator = build_runtime_policy_services(config, retriever=policy_retriever)
        app.state.policy_retriever = retriever
        app.state.policy_evaluator = policy_evaluator

        async def get_verified_domain_facts(account_number=None, state=None):
            cust = caller()
            if state and account_number and state.get('session_id'):
                binding = get(state['session_id']).bindings.get(account_number, {})
                account_number = binding.get('value', account_number)
            if not account_number and len(cust.get('accounts', [])) == 1:
                account_number = cust['accounts'][0]
            facts = {
                "accounts": cust.get("accounts", []),
                "caller_cards": cust.get("cards", []),
                "account_number": account_number,
                "has_blocked_card": False,
                "blocked_cards": [],
                "temporary_freeze": False,
                "compromised": False,
            }
            if account_number:
                for c_card in cust.get("cards", []):
                    if isinstance(c_card, dict) and c_card.get("account_number") == account_number:
                        if c_card.get("status") == "blocked":
                            if (state and state.get('last_domain_event') == 'card_unblocked_success'
                                    and str(c_card.get('id')) == str(state.get('verified_domain_facts', {}).get('last_unblocked_card_id'))):
                                continue  # This workflow has a newer, actual API result.
                            facts["has_blocked_card"] = True
                            facts["blocked_cards"].append(c_card)
                            reason = (c_card.get("reason") or "").lower()
                            if any(t in reason for t in ("lost", "stolen", "fraud", "compromised", "destroyed")):
                                facts["compromised"] = True
                            elif any(t in reason for t in ("freeze", "frozen", "temporary")):
                                facts["temporary_freeze"] = True

                if hasattr(app.state, "fincrime_service") and app.state.fincrime_service:
                    fc = app.state.fincrime_service
                    if fc.is_account_blocked(account_number):
                        facts["has_blocked_card"] = True
                    if fc.has_temporary_freeze(account_number):
                        facts["temporary_freeze"] = True
                    if fc.has_compromised_cards(account_number):
                        facts["compromised"] = True
                    blocked_records = fc.store.get_blocked_cards_for_account(account_number)
                    for r in blocked_records:
                        facts["blocked_cards"].append({
                            "id": r.card_id, "account_number": r.account_number,
                            "status": r.status, "reason": r.reason,
                        })
            facts['is_temporary_freeze'] = facts['temporary_freeze']
            facts['is_permanently_compromised'] = facts['compromised']
            if account_number and hasattr(app.state.gateway, 'policy_facts'):
                remote = await app.state.gateway.policy_facts(account_number)
                if remote is not None:
                    if not isinstance(remote, dict) or remote.get('account_number') != account_number:
                        raise ValueError('Invalid verified policy facts')
                    facts.update(remote)
            return facts

        app.state.facts_retriever = get_verified_domain_facts
        app.state.orchestrator = AgentOrchestrator(
            app.state.model, cards_agent, fincrime_agent,
            check_blocked_fn=check_account_has_blocked,
            agent_tools_policy=get_agent_tools_policy(config),
            checkpointer=checkpointer,
            policy_retriever=retriever,
            policy_evaluator=policy_evaluator,
            facts_retriever_fn=get_verified_domain_facts,
        )
        app.state.agent = app.state.orchestrator
        try:
            if gateway is not None:
                app.state.gateway = gateway
                recover()
                yield
            else:
                async with connect(config) as connected:
                    app.state.gateway = connected
                    recover()
                    yield
        finally:
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.state.model.close()
            journal.close()
            if hasattr(checkpointer, 'close'):
                checkpointer.close()

    app = FastAPI(title='Card Agent API', version='1.0', lifespan=lifespan)
    protect(app, config, 'agent', 'agent:invoke', 'experience-service')
    from card_chat.observability import instrument
    instrument(app, config, 'agent')
    app.state.journal = journal

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': 'Invalid request fields. Use the secure action form.'}, status_code=422)

    def owner():
        return (principal.get() or {}).get('sub', 'isolated-test')

    def caller():
        if app.state.security.enabled:
            return principal.get() or {}
        return config.get('LOCAL_CUSTOMER', {'sub': owner(), 'permissions': ['tools:execute']})

    async def check_account_has_blocked(account_num):
        cards = caller().get('cards', [])
        for c in cards:
            if isinstance(c, dict) and c.get('account_number') == account_num and c.get('status') == 'blocked':
                return True
        if hasattr(app.state, 'fincrime_service') and app.state.fincrime_service:
            return app.state.fincrime_service.is_account_blocked(account_num)
        return False

    def permitted():
        names = config.get('MODEL_ALLOWED_TOOLS')
        return eligible(app.state.gateway.catalogue, caller(), names.split(',') if names is not None else None)

    def private_values(c):
        values = [item['value'] for item in c.bindings.values()]
        # Old durable drafts/proposals are not trusted model context either.
        for tool, data in ((c.draft.get('tool'), c.draft.get('arguments', {})),
                           (c.proposal.name if c.proposal else None, c.proposal.arguments if c.proposal else {})):
            spec = app.state.gateway.catalogue.get(tool)
            safe_fields = spec.entry.context_fields if spec else []
            values.extend(value for key, value in data.items() if key not in safe_fields
                          and isinstance(value, (str, int)) and not REFERENCE.fullmatch(str(value)))
        values.extend(caller().get('accounts', []))
        values.append(caller().get('username'))
        return values

    def save(cid, c):
        data = {k: v for k, v in vars(c).items() if k != 'lock'}
        if c.proposal:
            data['proposal'] = asdict(c.proposal)
        journal.save(cid, owner(), data)

    @app.middleware('http')
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def get(cid):
        data = journal.load(cid, owner())
        if data is None:
            raise HTTPException(404, 'Conversation not found. Start a new conversation.')
        if data['proposal']:
            data['proposal'] = ProposedToolCall(**data['proposal'])
        return Conversation(**data)

    def reply(c, text, *, language=False, input_tool=None):
        c.history.append({'role': 'assistant', 'content': text if language else 'Action workflow updated.',
                          'kind': 'model' if language else 'workflow'})
        spec = app.state.gateway.catalogue[c.proposal.name] if c.proposal else None
        can_confirm = bool(c.review) and not (c.submission and c.submission['result']) and not (c.submission and c.submission['attempted']
                                             and not c.submission['result'] and not spec.safe_retry)
        return {'message': text, 'review': c.review, 'can_confirm': can_confirm,
                'confirmation_label': ('Block and replace this card' if spec and spec.name == 'block_card' and c.compound_approval
                                       else (spec.entry.confirmation_label or spec.entry.label) if spec else None),
                'usage': dict(c.usage), 'input_tool': input_tool}

    async def review_compound(cid, c):
        c.compound_approval = None
        if not c.proposal or c.proposal.name != 'block_card' or 'replace_card' not in permitted():
            return ''
        snapshot = await app.state.orchestrator.workflow.aget_state(
            {'configurable': {'thread_id': f'{owner()}:{cid}'}})
        if snapshot.values.get('planned_intents') != ['replace_card']:
            return ''
        c.compound_approval = {'card_id': c.proposal.arguments['card_id'],
                               'replacement_reason': 'Customer requested replacement', 'status': 'reviewed'}
        return ' This confirmation also authorizes issuing a replacement for this same card after blocking succeeds.'

    async def execute(cid, c):
        proposal = c.proposal
        spec = app.state.gateway.catalogue[proposal.name]
        if proposal.name not in permitted():
            raise HTTPException(403, 'Action is not permitted')
        execution_caller = caller()
        approval = c.compound_approval
        if (proposal.name == 'replace_card' and approval and approval.get('status') == 'replacing'
                and proposal.arguments.get('card_id') == approval.get('card_id')):
            # The immediately preceding successful API result is newer than the
            # signed card summary carried by this same HTTP request.
            execution_caller = {**execution_caller, 'cards': [
                {**card, 'status': 'blocked'} if isinstance(card, dict) and card.get('id') == approval['card_id'] else card
                for card in execution_caller.get('cards', [])]}
        authorise(spec, proposal.arguments, execution_caller)
        if c.submission and c.submission['result'] is not None:
            return reply(c, c.submission['result'])
        if proposal.name in ('issue_card', 'create_card', 'replace_card', 'block_card', 'unblock_card'):
            graph_config = {'configurable': {'thread_id': f'{owner()}:{cid}'}}
            snapshot = await app.state.orchestrator.workflow.aget_state(graph_config)
            facts = await app.state.facts_retriever(proposal.arguments.get('account_number'), state=snapshot.values)
            card_id = proposal.arguments.get('card_id')
            for card in caller().get('cards', []):
                if isinstance(card, dict) and str(card.get('id', card.get('card_id'))) == str(card_id):
                    facts = await app.state.facts_retriever(card.get('account_number'), state=snapshot.values)
                    break
            decision = app.state.policy_evaluator.validate_execution(
                proposal.name, proposal.arguments, facts, snapshot.values)
            if not decision.permitted:
                return reply(c, decision.decision_reason)
        if c.submission is None:
            c.submission = {'tool': proposal.name, 'arguments': spec.validate(proposal.arguments),
                            'key': uuid4().hex if spec.entry.idempotency_header else None,
                            'result': None, 'attempted': False}
        submission = c.submission
        if submission['result'] is not None:
            return reply(c, submission['result'])
        if submission['attempted'] and not spec.safe_retry:
            return reply(c, 'This action cannot be safely retried. Check its status before starting another request.')
        submission['attempted'] = True
        # Durable exact arguments and key precede the first write and every retry.
        save(cid, c)
        suffix = ('Click the confirmation button to retry the same submission safely.' if spec.safe_retry
                  else 'The outcome may be uncertain. This action cannot be safely retried; check its status first.')
        try:
            # Execute the reviewed snapshot directly. Confirmation makes no model call.
            calling_agent = None
            if hasattr(spec.entry, 'agents') and spec.entry.agents:
                calling_agent = spec.entry.agents[0]
            elif spec.name in ('block_card', 'unblock_card'):
                calling_agent = 'fincrime_agent'
            else:
                calling_agent = 'cards_agent'
            try:
                result = await app.state.gateway.execute(
                    submission['tool'], submission['arguments'], submission['key'], agent=calling_agent
                )
            except TypeError:
                result = await app.state.gateway.execute(
                    submission['tool'], submission['arguments'], submission['key']
                )
            if result.get('ok') is not True:
                error = result.get('error', 'Execution failed.')
                if not isinstance(error, str) or sanitise(error, private_values(c)) != error:
                    error = 'Execution failed.'
                if hasattr(app.state, 'orchestrator'):
                    thread_id = f"{owner()}:{cid}"
                    await app.state.orchestrator.resume_execution(
                        thread_id, owner(), cid, submission['tool'], result, ok=False, error=error
                    )
                return reply(c, error + ' ' + suffix)
            data = result.get('result')
            if not isinstance(data, dict):
                raise ValueError('Invalid structured API result')
            fields = '; '.join(f'{label}: {mask(data[key]) if ("account" in key or "card_number" in key) and not str(data[key]).startswith("SIM-") else data[key]}'
                               for key, label in spec.entry.result_labels.items() if key in data)
            text = f'{spec.entry.label} succeeded.' + (' ' + fields if fields else '')
            if proposal.name == 'replace_card' and approval and approval.get('status') == 'replacing':
                text = approval['block_result'] + ' ' + text
                approval['status'] = 'complete'
            issuance_outcome = result.get('issuance_outcome', data.get('outcome'))
            if spec.name in ('create_card', 'issue_card') and issuance_outcome == 'already_exists':
                text = ('A card already exists for this account. No new card was created. '
                        'To request another card, start a new action, select the additional-card option and confirm.'
                        + (' ' + fields if fields else ''))
            submission['result'] = text
            c.usage['completed_actions'] = c.usage.get('completed_actions', 0) + 1
            metrics = {'tool': spec.name, **c.usage}
            logger.warning('workflow_usage %s', json.dumps(metrics, sort_keys=True),
                           extra={'telemetry_event': 'workflow_usage', 'telemetry_fields': metrics})

            # Resume LangGraph compound multi-agent workflow with actual execution result
            resumed_turn = None
            if hasattr(app.state, 'orchestrator'):
                thread_id = f"{owner()}:{cid}"
                resumed_turn = await app.state.orchestrator.resume_execution(
                    thread_id, owner(), cid, submission['tool'], result, ok=True, message=text
                )

            if isinstance(resumed_turn, ProposedToolCall):
                if (proposal.name == 'block_card' and approval and approval.get('status') == 'confirmed'
                        and resumed_turn.name == 'replace_card'
                        and resumed_turn.arguments.get('card_id') == approval['card_id']):
                    arguments = {'card_id': approval['card_id'], 'reason': approval['replacement_reason']}
                    app.state.gateway.catalogue['replace_card'].validate(arguments)
                    approval.update(status='replacing', block_result=text)
                    ref = bind(c.bindings, cid, owner(), 'replace_card', 'card_id', approval['card_id'])
                    c.draft = {'tool': 'replace_card', 'arguments': {**arguments, 'card_id': ref}}
                    c.proposal = ProposedToolCall('replace_card', arguments)
                    c.submission = None
                    save(cid, c)  # Persist the reviewed continuation before its API write.
                    return await execute(cid, c)
                # Next compound intent requires separate human confirmation!
                next_spec = app.state.gateway.catalogue.get(resumed_turn.name)
                next_proposal_args = dict(resumed_turn.arguments)
                if next_spec and 'card_id' in next_proposal_args:
                    raw_card_id = next_proposal_args['card_id']
                    if not str(raw_card_id).startswith('selected_card_') and not str(raw_card_id).startswith('selected_input_'):
                        ref = bind(c.bindings, cid, owner(), resumed_turn.name, 'card_id', raw_card_id)
                        next_proposal_args['card_id'] = ref
                c.draft = {'tool': resumed_turn.name, 'arguments': next_proposal_args}
                c.proposal = ProposedToolCall(resumed_turn.name, resumed_turn.arguments)
                c.submission = None  # Reset submission so the next action can be confirmed
                c.review = uuid4().hex  # Fresh review nonce for second confirmation
                save(cid, c)
                masked_details = '; '.join(f'{label}: {value}' for label, value in next_spec.masked(resumed_turn.arguments).items()) if next_spec else ''
                combined_msg = f"{text} Next step: Please review: {next_spec.entry.label}. {masked_details}. Confirm with the button or tell me what to change."
                response = reply(c, combined_msg)
                response['can_confirm'] = True
                response['review'] = c.review
                response['confirmation_label'] = (next_spec.entry.confirmation_label or next_spec.entry.label) if next_spec else None
                return response

            if isinstance(resumed_turn, CustomerReply) and resumed_turn.text and resumed_turn.text != text:
                if issuance_outcome != 'already_exists':
                    text = f"{text} {resumed_turn.text}"

            response = reply(c, text)
            if proposal.name in ('view_accounts', 'view_cards'):
                response['data'] = data
            return response
        except Exception:
            return reply(c, 'Could not complete the action. ' + suffix)

    @app.get('/health')
    async def health():
        journal.db.execute('SELECT 1')
        return {'status': 'ok'}

    @app.get('/')
    async def index():
        return FileResponse(Path(__file__).with_name('index.html'))

    @app.post('/entry/messages', response_model=Reply)
    async def entry_message(body: Message):
        pii_res = app.state.pii_gateway.sanitize_input(body.text, owner="entry", session_id="entry") if hasattr(app.state, "pii_gateway") else None
        safe = sanitise(pii_res.text) if pii_res else sanitise(body.text)
        if (pii_res and pii_res.secrets_detected) or safe == PRIVATE_PASTE or (not pii_res and safe != body.text):
            return Reply(message=PRIVATE_NOTICE)
        tools = {name: {'name': name, 'description': description,
                 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
                 for name, description in [('register', 'Open the customer registration form.'),
                                            ('login', 'Open the customer login form.')]}
        try:
            turn = await app.state.agent.interpret_turn(
                [{'role': 'user', 'content': safe}], {}, tools,
                thread_id="entry:entry", owner="entry", session_id="entry"
            )
        except Exception:
            raise HTTPException(503, 'Language service unavailable. Choose Register or Login.') from None
        if isinstance(turn, ProposedToolCall):
            return Reply(message='Please complete the secure form below.', input_tool=turn.name)
        return Reply(message=sanitise(turn.text))

    @app.post('/conversations', response_model=Reply)
    async def start():
        cid = uuid4().hex
        c = Conversation()
        result = {'id': cid, **reply(c, 'Hi! What would you like help with?')}
        save(cid, c)
        return result

    async def message(cid: str, body: Message, c):
        async with c.lock:
            if c.submission:
                return reply(c, 'This submission is fixed. Use its confirmation button for a supported retry, or start a new conversation.')
            # Every new turn invalidates any previous review before interpreting updates.
            c.review, c.proposal = None, None
            c.compound_approval = None
            if hasattr(app.state, 'pii_gateway'):
                pii_res = app.state.pii_gateway.sanitize_input(body.text, owner=owner(), session_id=cid, private_values=tuple(private_values(c)))
                for tok, raw_val in pii_res.tokens_created.items():
                    if tok not in c.bindings:
                        prefix = 'account' if 'account' in tok else 'input'
                        c.bindings[tok] = {'cid': cid, 'owner': owner(), 'tool': c.draft.get('tool', ''), 'field': prefix, 'value': raw_val}
                safe_text = sanitise(pii_res.text, private_values(c))
                if pii_res.secrets_detected or safe_text == PRIVATE_PASTE or (not pii_res and safe_text != body.text):
                    c.history.append({'role': 'user', 'content': PRIVATE_PASTE, 'kind': 'chat'})
                    return reply(c, PRIVATE_NOTICE)
            else:
                safe_text = sanitise(body.text, private_values(c))
                if safe_text == PRIVATE_PASTE or safe_text != body.text:
                    c.history.append({'role': 'user', 'content': safe_text, 'kind': 'chat'})
                    return reply(c, PRIVATE_NOTICE)
            c.history.append({'role': 'user', 'content': safe_text, 'kind': 'chat'})
            if ambiguous_old_reference(safe_text):
                return reply(c, 'Please select the intended account or details again in the secure form; I cannot safely infer an older selection.')
            catalogue = permitted()
            if not catalogue:
                return reply(c, 'No eligible actions are available for this caller.')
            verified_draft = {}
            if c.draft.get('tool') in catalogue:
                try:
                    draft_spec = catalogue[c.draft['tool']]
                    draft_values = resolve(draft_spec, c.draft.get('arguments', {}), c.bindings, cid, owner())
                    authorise(draft_spec, draft_values, caller())
                    verified_draft = c.draft
                except (ValueError, HTTPException):
                    pass  # Unbound, legacy or revoked facts never enter model context.
            model_tools = tools_for_model(catalogue)
            if config.get('MODEL_ALLOWED_TOOLS') is None:
                model_tools['logout'] = {'name': 'logout', 'description': 'Ask the customer to confirm signing out.',
                    'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
            history, draft, tools = build_context([turn for turn in c.history if turn.get('kind') in ('chat', 'model')],
                verified_draft, model_tools, private_values=private_values(c))
            c.usage['model_calls'] = c.usage.get('model_calls', 0) + 1
            try:
                turn = await app.state.agent.interpret_turn(
                    history, draft, tools,
                    thread_id=f"{owner()}:{cid}", owner=owner(), session_id=cid
                )
            except Exception as exc:
                logger.exception("Language service / interpret_turn failed: %s", exc)
                return reply(c, 'The language service is unavailable or proposed an invalid action. Please try again.')
            finally:
                for key, value in getattr(app.state.model, 'last_usage', {}).items():
                    if key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'):
                        c.usage[key] = c.usage.get(key, 0) + value
            if turn is None:
                return reply(c, 'The language service is unavailable or proposed an invalid action. Please try again.')
            if isinstance(turn, CustomerReply):
                # Numeric policy identifiers resemble private PINs to the conservative
                # chat scrubber. Keep exact citations in graph state and render names.
                text = turn.text
                for number in range(1, 5):
                    text = text.replace(f'POL-{number:03}', f'banking policy {number}')
                return reply(c, sanitise(text, private_values(c)), language=True)
            if turn.name == 'logout':
                return reply(c, 'Confirm signing out using the button below.', input_tool='logout')
            spec = app.state.gateway.catalogue[turn.name]
            graph_config = {'configurable': {'thread_id': f'{owner()}:{cid}'}}
            snapshot = await app.state.orchestrator.workflow.aget_state(graph_config)
            decision = snapshot.values.get('policy_evaluation', {})
            if decision.get('customer_decision') == 'accept' and decision.get('recommended_intent') == turn.name:
                # Only the evaluator's verified target may become a new selection.
                # Ordinary model-supplied raw identifiers still fail resolution.
                context = decision.get('recommended_context', {})
                for field in ('card_id', 'account_number'):
                    value = context.get(field)
                    if value and turn.arguments.get(field) == value and field in spec.input_schema['properties']:
                        turn.arguments[field] = bind(c.bindings, cid, owner(), turn.name, field, value)
                turn = ProposedToolCall(turn.name, {
                    key: value for key, value in turn.arguments.items()
                    if key in spec.input_schema['properties']})
            if turn.name == 'issue_card' and not caller().get('accounts') and 'create_account' in catalogue:
                c.draft = {'tool': 'create_account', 'arguments': {}}
                return reply(c, 'Create an account first. You can choose to issue a card with it.', input_tool='create_account')
            if turn.name in ('block_card', 'unblock_card', 'replace_card') and 'card_id' not in turn.arguments:
                import re
                ending_match = re.search(r'ending in\s+([0-9a-zA-Z]{4})', safe_text, re.IGNORECASE)
                if ending_match:
                    last4 = ending_match.group(1).lower()
                    for card in caller().get('cards', []):
                        is_blocked = isinstance(card, dict) and card.get('status') == 'blocked'
                        if turn.name in ('unblock_card', 'replace_card') and not is_blocked:
                            continue
                        if turn.name == 'block_card' and is_blocked:
                            continue
                        num = (card.get('card_number') if isinstance(card, dict) else str(card)).lower()
                        cid_val = card.get('id') if isinstance(card, dict) else str(card)
                        if num.endswith(last4) or cid_val.endswith(last4):
                            ref = bind(c.bindings, cid, owner(), turn.name, 'card_id', cid_val)
                            turn.arguments['card_id'] = ref
                            break
            if turn.name in ('issue_card', 'create_card'):
                account_ref = turn.arguments.get('account_number')
                account_val = c.bindings.get(account_ref, {}).get('value', account_ref) if account_ref else None
                if not account_val and verified_draft.get('tool') == turn.name:
                    draft_ref = verified_draft.get('arguments', {}).get('account_number')
                    if draft_ref:
                        account_val = c.bindings.get(draft_ref, {}).get('value', draft_ref)
                if account_val and await check_account_has_blocked(account_val):
                    c.draft = {}
                    return reply(c, f'Cannot issue a new card for account {mask(account_val)} because an existing card for this account is blocked. Under banking security rules, the blocked card must be resolved before a new card can be issued.')
            proposal_arguments = {**(verified_draft.get('arguments', {}) if verified_draft.get('tool') == turn.name else {}),
                                  **turn.arguments}
            try:
                partial = resolve(spec, proposal_arguments, c.bindings, cid, owner())
                authorise(spec, partial, caller())
                missing = spec.missing(partial)
                if missing:
                    c.draft = {'tool': turn.name, 'arguments': proposal_arguments}
                    name = missing[0]
                    label = spec.entry.field_labels.get(name, spec.input_schema['properties'][name].get('title', name))
                    return reply(c, f'I can help with {spec.entry.label.lower()}. Please complete the private details below.',
                                 input_tool=turn.name)
                arguments = spec.validate(partial)
            except ValueError:
                return reply(c, 'Please supply private details using the secure action form and use its current selection.',
                             input_tool=turn.name)
            c.draft = {'tool': turn.name, 'arguments': proposal_arguments}
            c.proposal = ProposedToolCall(turn.name, arguments)
            if spec.entry.confirmation == 'none':
                response = await execute(cid, c)
                c.proposal, c.submission, c.review, c.draft = None, None, None, {}
                response.update(review=None, can_confirm=False, confirmation_label=None)
                return response
            c.review = uuid4().hex
            compound_notice = await review_compound(cid, c)
            details = '; '.join(f'{label}: {value}' for label, value in spec.masked(arguments).items())
            if spec.name == 'unblock_card':
                prompt_warning = (
                    f'Please review: {spec.entry.label}. {details}. '
                    f'ATTENTION: Unblocking this card will restore its active status and re-enable new card issuance on this account. '
                    f'Confirm with the button or tell me what to change.'
                )
                return reply(c, prompt_warning)
            return reply(c, f'Please review: {spec.entry.label}. {details}.{compound_notice} Confirm with the button or tell me what to change.')

    @app.get('/conversations/{cid}/inputs', response_model=InputOptions, response_model_exclude_none=True)
    async def input_options(cid: str):
        async with worker_lock:
            c = get(cid)
            result = form_options(permitted(), caller(), c.bindings, cid, owner())
            save(cid, c)
            return result

    async def structured(cid, body, c):
        if c.submission:
            return reply(c, 'This submission is fixed. Retry it safely or start a new conversation.')
        c.review, c.proposal = None, None
        c.compound_approval = None
        # An accepted correction invalidates the previous review even when its
        # replacement details fail validation. Do not persist unvalidated input.
        save(cid, c)
        spec = permitted().get(body.tool)
        if spec is None:
            raise HTTPException(403, 'Action is not permitted')
        references = dict(c.draft.get('arguments', {})) if c.draft.get('tool') == body.tool else {}
        if set(body.fields) & set(spec.entry.trusted_fields) or set(body.fields) & set(body.selections):
            raise HTTPException(422, 'Use a trusted selection for protected fields')
        try:
            for key, value in body.fields.items():
                if key not in spec.input_schema['properties']:
                    raise ValueError('Unknown input')
                if key in spec.entry.context_fields:
                    if isinstance(value, str) and sanitise(value) != value:
                        raise ValueError('Private text is not model-visible')
                    references[key] = value
                else:
                    references[key] = bind(c.bindings, cid, owner(), body.tool, key, value)
            for key, ref in body.selections.items():
                # A caller can only select an existing binding; cannot create an identifier.
                if key in spec.entry.context_fields:
                    raise ValueError('Use a field, not a selection, for public text')
                resolve(spec, {key: ref}, c.bindings, cid, owner())
                references[key] = ref
            for key, source in spec.entry.trusted_fields.items():
                if source == 'username':
                    if not caller().get('username'):
                        raise HTTPException(403, 'Verified customer identity required')
                    references[key] = bind(c.bindings, cid, owner(), body.tool, key, caller()['username'])
            values = resolve(spec, references, c.bindings, cid, owner())
            authorise(spec, values, caller())
            missing = spec.missing(values)
            c.draft = {'tool': body.tool, 'arguments': references}
            if missing:
                return reply(c, 'Please complete the required fields in the secure action form.', input_tool=body.tool)
            arguments = spec.validate(values)
            if body.tool in ('issue_card', 'create_card'):
                account_val = values.get('account_number')
                if account_val and await check_account_has_blocked(account_val):
                    return reply(c, f'Cannot issue a new card for account {mask(account_val)} because an existing card for this account is blocked. Under banking security rules, the blocked card must be resolved before a new card can be issued.')
        except ValueError:
            raise HTTPException(422, 'Structured details or selections are invalid') from None
        c.proposal = ProposedToolCall(body.tool, arguments)
        if spec.entry.confirmation == 'none':
            result = await execute(cid, c)
            c.proposal, c.submission, c.review, c.draft = None, None, None, {}
            result.update(review=None, can_confirm=False, confirmation_label=None)
            return result
        c.review = uuid4().hex
        compound_notice = await review_compound(cid, c)
        details = '; '.join(f'{label}: {value}' for label, value in spec.masked(arguments).items())
        return reply(c, f'Please review: {spec.entry.label}. {details}.{compound_notice} Confirm with the button or update the secure form.')

    async def confirm(cid: str, body: Confirmation, c):
        async with c.lock:
            if not c.review or body.review != c.review or c.proposal is None:
                raise HTTPException(409, 'Review changed. Read the latest review before confirming.')
            if c.proposal.name == 'block_card' and c.compound_approval and c.compound_approval.get('status') == 'reviewed':
                if c.proposal.arguments.get('card_id') != c.compound_approval['card_id']:
                    raise HTTPException(409, 'Review changed. Read the latest review before confirming.')
                c.compound_approval['status'] = 'confirmed'
            return await execute(cid, c)

    async def run_operation(oid):
        # A single worker serializes state and jobs, including competing HTTP calls.
        async with worker_lock:
            row = journal.db.execute('SELECT * FROM operations WHERE id=?', (oid,)).fetchone()
            if row['status'] not in ('pending', 'running'):
                return
            pt = principal.set({'sub': row['owner']})
            dt = delegation.set(row['delegation'])
            tt = trace.set(row['trace'])
            journal.db.execute("UPDATE operations SET status='running' WHERE id=?", (oid,))
            journal.db.commit()
            try:
                # Reload auth from the persisted signed assertion; expired jobs fail closed.
                if app.state.security.enabled:
                    claims = app.state.security.customer(row['delegation'], 'agent')
                    if claims['sub'] != row['owner']:
                        raise HTTPException(403, 'Customer mismatch')
                    principal.set(claims)
                body = json.loads(row['body'])
                c = get(row['cid'])
                if row['kind'] == 'messages':
                    result = await message(row['cid'], Message(**body), c)
                elif row['kind'] == 'inputs':
                    result = await structured(row['cid'], StructuredInput(**body), c)
                else:
                    result = await confirm(row['cid'], Confirmation(**body), c)
                # State and operation result commit atomically. execute() already saved write intent.
                if c is not None:
                    data = {k: v for k, v in vars(c).items() if k != 'lock'}
                    if c.proposal:
                        data['proposal'] = asdict(c.proposal)
                    journal.db.execute('UPDATE conversations SET data=? WHERE id=? AND owner=?',
                                       (json.dumps(data), row['cid'], row['owner']))
                journal.db.execute("UPDATE operations SET status='succeeded',result=? WHERE id=?",
                                   (json.dumps(result), oid))
                journal.db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = exc.detail if isinstance(exc, HTTPException) else 'Operation failed; retry with a new request key.'
                code = exc.status_code if isinstance(exc, HTTPException) else 503
                journal.db.execute("UPDATE operations SET status='failed',error=? WHERE id=?",
                                   (json.dumps({'detail': error, 'status_code': code}), oid))
                journal.db.commit()
            finally:
                principal.reset(pt)
                delegation.reset(dt)
                trace.reset(tt)

    def schedule(oid):
        task = asyncio.create_task(run_operation(oid))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    def recover():
        for row in journal.db.execute("SELECT id FROM operations WHERE status='pending'").fetchall():
            schedule(row['id'])

    def operation(oid):
        row = journal.db.execute('SELECT * FROM operations WHERE id=? AND owner=?', (oid, owner())).fetchone()
        if row is None:
            raise HTTPException(404, 'Operation not found')
        return {'id': oid, 'status_url': '/operations/' + oid, 'status': row['status'],
                'result': json.loads(row['result']) if row['result'] else None,
                'error': json.loads(row['error'])['detail'] if row['error'] else None}

    @app.get('/operations/{oid}', response_model=Operation)
    async def status(oid: str):
        return operation(oid)

    async def submit(cid, body, kind, request):
        get(cid)  # Authorize before accepting or returning a deduplicated operation.
        key = request.headers.get('Idempotency-Key') or uuid4().hex
        if len(key) > 128:
            raise HTTPException(422, 'Request key too long')
        original = json.dumps(body.model_dump(), sort_keys=True)
        if kind == 'messages':
            c = get(cid)
            safe = sanitise(body.text, private_values(c))
            body = Message(text=safe if safe == body.text else PRIVATE_PASTE)
        encoded = json.dumps(body.model_dump(), sort_keys=True)
        # Rejected private pastes are equivalent safe notices, not stored secrets
        # or dictionary-attackable hashes of PINs/credentials.
        request_hash = hashlib.sha256(encoded.encode()).hexdigest()
        old = journal.db.execute('SELECT * FROM operations WHERE owner=? AND cid=? AND request_key=?',
                                 (owner(), cid, key)).fetchone()
        if old and (old['kind'] != kind or (old['request_hash'] or hashlib.sha256(old['body'].encode()).hexdigest()) != request_hash):
            raise HTTPException(409, 'Request key already used for a different operation')
        oid = old['id'] if old else uuid4().hex
        if not old:
            journal.db.execute('INSERT INTO operations(id,owner,cid,kind,request_key,body,delegation,trace,status,request_hash) '
                               "VALUES(?,?,?,?,?,?,?,?,'pending',?)",
                               (oid, owner(), cid, kind, key, encoded, delegation.get(), trace.get(), request_hash))
            journal.db.commit()  # 202 can only follow durable acceptance.
        if request.headers.get('Prefer') == 'respond-async':
            if not old:
                schedule(oid)
            return JSONResponse(operation(oid), status_code=202, headers={'Location': '/operations/' + oid})
        if not old or old['status'] in ('pending', 'running'):
            await schedule(oid)
        result = operation(oid)
        if result['status'] == 'failed':
            row = journal.db.execute('SELECT error FROM operations WHERE id=?', (oid,)).fetchone()
            error = json.loads(row['error'])
            raise HTTPException(error['status_code'], error['detail'])
        return Reply(**result['result']).model_dump(exclude={'id'})

    @app.post('/conversations/{cid}/messages', response_model=Reply, responses={202: {'model': Operation}})
    async def submit_message(cid: str, body: Message, request: Request):
        return await submit(cid, body, 'messages', request)

    @app.post('/conversations/{cid}/confirm', response_model=Reply, responses={202: {'model': Operation}})
    async def submit_confirmation(cid: str, body: Confirmation, request: Request):
        return await submit(cid, body, 'confirm', request)

    @app.post('/conversations/{cid}/inputs', response_model=Reply, responses={202: {'model': Operation}})
    async def submit_inputs(cid: str, body: StructuredInput, request: Request):
        return await submit(cid, body, 'inputs', request)

    return app


app = create_app()
