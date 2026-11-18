"""Capture actual Anthropic SDK requests and prove grounded, non-executing policy steps."""
import asyncio
from copy import deepcopy
import hashlib
import json

from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient
import httpx
import pytest

from card_chat.catalogue import load_catalogue
from card_chat.main import create_app
from card_chat.policy.adapters.document_source import InMemoryDocumentSource
from card_chat.policy.factory import build_policy_evaluator
from card_chat.policy.pipeline.ingestion import PolicyIngestionPipeline
from card_chat.policy.recommendations import PolicyNextStepPlanner, RecommendationUnavailable
from card_chat.provider import AnthropicModel

pytestmark = pytest.mark.live_policy


def set_policy(retriever, tmp_path, offer=False, extra=''):
    documents = []
    for doc in retriever._doc_source.list_documents():
        if doc.policy_id == 'POL-003':
            text = doc.raw_content.replace('version: 1.0.0', 'version: 1.0.1')
            text = text.replace('offer_on_temporary_freeze: false',
                                'offer_on_temporary_freeze: ' + str(offer).lower()) + extra
            if offer:
                text = text.replace('proactive_offer_only_for_compromised_cards', 'require_customer_acceptance')
                text = text.replace(
                    'a replacement card is **not** proactively offered because the card is intended to be unblocked by the customer when located.',
                    'under this approved revision a replacement card is proactively offered for temporary freezes, with separate customer acceptance and confirmation.')
            doc = doc.model_copy(update={'version': '1.0.1', 'raw_content': text,
                'conditions': {**doc.conditions, 'offer_on_temporary_freeze': offer},
                'content_hash': hashlib.sha256(text.encode()).hexdigest()})
        documents.append(doc)
    retriever._doc_source = InMemoryDocumentSource(documents)
    PolicyIngestionPipeline(retriever._doc_source, retriever._embedder, retriever._store,
                            audit_log_path=tmp_path/'recommendation-audit.json').ingest_all()


def reply_for(context):
    post = context['stage'] == 'after_block'
    policy = next(p for p in context['passages'] if p['policy_id'] == ('POL-003' if post else 'POL-001'))
    if post:
        offer = not context['facts']['is_temporary_freeze'] or policy['conditions']['offer_on_temporary_freeze']
        action = 'offer_replacement' if offer else 'none'
        text = ('Based on the retrieved policy, would you like to review a replacement card?' if offer
                else 'The retrieved policy supports keeping this temporary freeze without offering replacement.')
        quote = next(line.strip() for line in policy['text'].splitlines() if 'offer_on_temporary_freeze:' in line)
    else:
        action = 'offer_unblock'
        text = 'The policy describes a temporary-freeze remedy. Would you like to unblock it first?'
        quote = next(line.strip() for line in policy['text'].splitlines() if 'prohibit_issuance_if_any_card_blocked' in line)
    return {'action': action, 'message': text,
            'citations': [{'evidence_id': policy['evidence_id'], 'quote': quote}]}


def model_fixture(payloads, mutate=None, unavailable=False, shape=None):
    def transport(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        context = json.loads(payload['messages'][0]['content'])
        if 'stage' in context:
            if unavailable:
                return httpx.Response(503, json={'type':'error','error':{'type':'overloaded_error','message':'Unavailable'}})
            result = reply_for(context)
            if mutate:
                result = mutate(result, context)
            name = 'recommend_policy_next_step'
        else:
            # Explicit test double for ordinary specialist proposals. It receives
            # no raw card identifiers; secure forms select them in the application.
            name = context['collected_details'].get('tool') or 'block_card'
            result = {}
        content=[{'type':'tool_use','id':'call_test','name':name,'input':result}]
        stop='tool_use'
        if shape=='text':
            content=[{'type':'text','text':'Would you like a replacement?'}]
            stop='end_turn'
        elif shape=='wrong_tool':
            content[0]['name']='replace_card'
        elif shape=='multiple':
            content.append({**content[0],'id':'another_call'})
        elif shape=='truncated':
            stop='max_tokens'
        return httpx.Response(200, json={'id':'msg_test','type':'message','role':'assistant',
            'model':'synthetic-model','stop_reason':stop,'stop_sequence':None,
            'usage':{'input_tokens':101,'output_tokens':23},'content':content})
    model = AnthropicModel({'ANTHROPIC_API_KEY':'synthetic-not-live','MODEL_ID':'synthetic-model'})
    original = model.client
    model.client = AsyncAnthropic(api_key='synthetic-not-live', max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    asyncio.run(original.close())
    return model


class Gateway:
    def __init__(self, customer):
        self.catalogue = load_catalogue()
        self.calls = []
        self.customer = customer
    async def execute(self, name, arguments, key=None, **kwargs):
        self.calls.append((name, deepcopy(arguments), key))
        card = self.customer['cards'][0]
        card['status'] = 'blocked' if name == 'block_card' else 'active'
        card['reason'] = arguments.get('reason','')
        return {'ok':True,'result':{'card_id':card['id'],'status':card['status'],
                                    'last_block_reason':card['reason']}}


def application(retriever, model):
    customer = {'sub':'recommendation-customer','username':'synthetic.owner',
        'accounts':['0012345678'],'permissions':['tools:execute'],
        'cards':[{'id':'private-card-reference','account_number':'0012345678',
                  'card_number':'SIM-private-card','status':'active'}]}
    gateway = Gateway(customer)
    app = create_app(model, gateway, policy_retriever=retriever,
        config={'ALLOW_INSECURE_LOCAL':'1','LOCAL_CUSTOMER':customer})
    return app, gateway


def block_review(client, cid, reason='temporary freeze', tool='block_card'):
    path = '/conversations/'+cid
    options = client.get(path+'/inputs').json()['tools']
    definition = next(t for t in options if t['name']==tool)
    ref = next(f for f in definition['fields'] if f['name']=='card_id')['options'][0]['ref']
    response = client.post(path+'/inputs',json={'tool':tool,'fields':{'reason':reason},'selections':{'card_id':ref}})
    assert response.status_code==200
    assert response.json()['can_confirm']
    return response.json()


def state(client,app,cid):
    return client.portal.call(app.state.orchestrator.workflow.aget_state,
        {'configurable':{'thread_id':f'isolated-test:{cid}'}}).values


@pytest.mark.parametrize('offer',[False,True])
@pytest.mark.parametrize('chat_first',[False,True])
def test_actual_sdk_receives_policies_and_model_controls_followup(policy_retriever,tmp_path,offer,chat_first):
    set_policy(policy_retriever,tmp_path,offer)
    payloads=[]
    app,gateway=application(policy_retriever,model_fixture(payloads))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        path='/conversations/'+cid
        if chat_first:
            assert client.post(path+'/messages',json={'text':'Temporarily freeze my card'}).status_code==200
        review=block_review(client,cid)
        assert not gateway.calls
        response=client.post(path+'/confirm',json={'review':review['review']},headers={'Idempotency-Key':'confirmed-freeze'})
        assert response.status_code==200
        assert len(gateway.calls)==1 and gateway.calls[0][0]=='block_card'
        assert response.json()['usage']['model_calls']==(2 if chat_first else 1)
        assert response.json()['usage']['input_tokens']==101*(2 if chat_first else 1)
        snapshot=state(client,app,cid)
        recommendation=snapshot['policy_evaluation']['recommendation']
        assert recommendation['mode']=='model'
        assert recommendation['action']==('offer_replacement' if offer else 'none')
        assert recommendation['citations'][0]['version']=='1.0.1'
        assert recommendation['citations'][0]['source'].endswith('POL-003-post-block-journey.md')
        assert bool(snapshot['pending_customer_question']) is offer
        assert reply_for(json.loads(payloads[-1]['messages'][0]['content']))['message'] in response.json()['message']
        requests=[p for p in payloads if 'stage' in json.loads(p['messages'][0]['content'])]
        assert len(requests)==1
        sent=json.loads(requests[0]['messages'][0]['content'])
        assert sent['facts']['has_blocked_card'] and sent['facts']['is_temporary_freeze']
        assert any(p['conditions'].get('offer_on_temporary_freeze') is offer for p in sent['passages'] if p['policy_id']=='POL-003')
        assert any('Temporary Customer Freeze' in p['text'] for p in sent['passages'])
        assert 'decision_reason' not in sent and 'pending_question' not in sent
        outbound=json.dumps(requests)
        for private in ('0012345678','private-card-reference','SIM-private-card','synthetic.owner','recommendation-customer'):
            assert private not in outbound
        assert [t['name'] for t in requests[0]['tools']]==['recommend_policy_next_step']
        assert requests[0]['tool_choice']=={'type':'auto','disable_parallel_tool_use':True}
        # Replaying a confirmed write does not regenerate a recommendation.
        replay=client.post(path+'/confirm',json={'review':review['review']},headers={'Idempotency-Key':'confirmed-freeze'})
        assert replay.json()==response.json() and len(payloads)==len(requests)+(1 if chat_first else 0)
        assert len(gateway.calls)==1
        if offer:
            accepted=client.post(path+'/messages',json={'text':'yes please'}).json()
            assert len(gateway.calls)==1
            if not accepted['can_confirm']:
                accepted=block_review(client,cid,'replacement requested',tool='replace_card')
            assert client.post(path+'/confirm',json={'review':accepted['review']}).status_code==200
            assert [call[0] for call in gateway.calls]==['block_card','replace_card']


@pytest.mark.parametrize('bad',[
    {'action':'offer_unblock'},
    {'action':'execute_replacement'},
    {'citations':[{'evidence_id':'evidence_l','quote':'a fabricated policy passage'}]},
    {'citations':[{'evidence_id':'evidence_a','quote':'a fabricated policy passage'}]},
    {'message':'Your account 0012345678 can receive a new card?'},
    {'message':'I have issued your replacement card. Would you like another?'},
    {'card_id':'injected-card'},
    {'citations':[{'evidence_id':'evidence_a','quote':' '*30}]},
    {'message':' '*30},
])
def test_bad_model_recommendations_never_trigger_or_rewrite_banking_action(policy_retriever,tmp_path,bad):
    set_policy(policy_retriever,tmp_path,True)
    payloads=[]
    app,gateway=application(policy_retriever,model_fixture(payloads,lambda value,_:{**value,**bad}))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        review=block_review(client,cid)
        response=client.post(f'/conversations/{cid}/confirm',json={'review':review['review']}).json()
        assert 'succeeded' in response['message'] and 'unavailable' in response['message']
        assert len(gateway.calls)==1
        snapshot=state(client,app,cid)
        assert not snapshot['pending_customer_question']
        assert snapshot['policy_evaluation']['policy_error']=='PolicyRecommendationUnavailable'


def test_model_failure_preserves_success_and_write_replay(policy_retriever,tmp_path):
    set_policy(policy_retriever,tmp_path,True)
    payloads=[]
    app,gateway=application(policy_retriever,model_fixture(payloads,unavailable=True))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        review=block_review(client,cid)
        path=f'/conversations/{cid}/confirm'
        headers={'Idempotency-Key':'committed-before-llm-failure'}
        response=client.post(path,json={'review':review['review']},headers=headers)
        assert response.status_code==200 and 'succeeded' in response.json()['message']
        assert 'unavailable' in response.json()['message']
        assert len(gateway.calls)==len(payloads)==1
        assert client.post(path,json={'review':review['review']},headers=headers).json()==response.json()
        assert len(gateway.calls)==len(payloads)==1


def test_model_can_abstain_without_application_inventing_an_offer(policy_retriever,tmp_path):
    set_policy(policy_retriever,tmp_path,True)
    message='The retrieved guidance needs clarification before a follow-up offer.'
    payloads=[]
    app,gateway=application(policy_retriever,model_fixture(payloads,lambda value,_:{**value,'action':'none','message':message}))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        review=block_review(client,cid)
        response=client.post(f'/conversations/{cid}/confirm',json={'review':review['review']}).json()
        assert message in response['message']
        assert not state(client,app,cid)['pending_customer_question']
        assert len(gateway.calls)==1


def test_private_values_in_policy_text_are_redacted_before_sdk_call(policy_retriever,tmp_path,caplog):
    embedded='sk-ant-accidentally-included-sensitive-value'
    set_policy(policy_retriever,tmp_path,True,'\nNever include this example key: '+embedded+'\n')
    payloads=[]
    app,gateway=application(policy_retriever,model_fixture(payloads))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        review=block_review(client,cid)
        response=client.post(f'/conversations/{cid}/confirm',json={'review':review['review']}).json()
        assert state(client,app,cid)['policy_evaluation']['recommendation']['mode']=='model',response
        assert embedded not in json.dumps(payloads) and '[redacted]' in json.dumps(payloads)
        assert embedded not in caplog.text


def test_permanent_compromise_cannot_be_recommended_for_unblocking(policy_retriever):
    evaluator=build_policy_evaluator(retriever=policy_retriever)
    facts={'has_blocked_card':True,'is_temporary_freeze':True,'is_permanently_compromised':True}
    evaluation=evaluator.evaluate_constraints('issue_card',facts=facts)
    class BadModel:
        async def recommend_policy(self,context):
            assert context['allowed_recommendations']==['none']
            return reply_for(context)
    with pytest.raises(RecommendationUnavailable,match='eligible'):
        asyncio.run(PolicyNextStepPlanner(BadModel()).recommend(evaluation,facts,'restricted_issuance',{'unblock_card':{}}))


def test_recommendation_timeout_is_bounded(policy_retriever):
    facts={'has_blocked_card':True}
    evaluation=build_policy_evaluator(retriever=policy_retriever).evaluate_constraints(
        None,facts=facts,last_domain_event='card_blocked_success')
    class SlowModel:
        async def recommend_policy(self,context):
            await asyncio.sleep(10)
    with pytest.raises(TimeoutError):
        asyncio.run(PolicyNextStepPlanner(SlowModel(),timeout=.01).recommend(
            evaluation,facts,'after_block',{'replace_card':{}}))


def test_explicit_freeze_prohibition_rejects_an_incorrect_model_offer(policy_retriever,tmp_path):
    set_policy(policy_retriever,tmp_path,False)
    payloads=[]
    def incorrect(value,context):
        return {**value,'action':'offer_replacement','message':'Would you like a replacement card?'}
    app,gateway=application(policy_retriever,model_fixture(payloads,incorrect))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        review=block_review(client,cid)
        response=client.post(f'/conversations/{cid}/confirm',json={'review':review['review']}).json()
        assert 'succeeded' in response['message'] and 'unavailable' in response['message']
        assert not state(client,app,cid)['pending_customer_question']
        assert len(gateway.calls)==len(payloads)==1
        assert response['usage']['model_calls']==1


@pytest.mark.parametrize('fault',['missing','unapproved'])
def test_missing_approved_evidence_blocks_write_and_never_calls_model(policy_retriever,tmp_path,fault):
    set_policy(policy_retriever,tmp_path,True)
    payloads=[]
    app,gateway=application(policy_retriever,model_fixture(payloads))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        review=block_review(client,cid)
        if fault=='missing':
            policy_retriever._store.delete_by_policy_id('POL-003')
        else:
            policy_retriever._doc_source=InMemoryDocumentSource([
                d.model_copy(update={'approved':False,'status':'draft'}) if d.policy_id=='POL-003' else d
                for d in policy_retriever._doc_source.list_documents()])
        response=client.post(f'/conversations/{cid}/confirm',json={'review':review['review']}).json()
        assert 'unavailable' in response['message'].lower()
        assert not gateway.calls and not payloads


@pytest.mark.parametrize('shape',['text','wrong_tool','multiple','truncated'])
def test_invalid_sdk_response_shape_keeps_confirmed_block(policy_retriever,tmp_path,shape):
    set_policy(policy_retriever,tmp_path,True)
    payloads=[]
    app,gateway=application(policy_retriever,model_fixture(payloads,shape=shape))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        review=block_review(client,cid)
        response=client.post(f'/conversations/{cid}/confirm',json={'review':review['review']}).json()
        assert 'succeeded' in response['message'] and 'unavailable' in response['message']
        assert not state(client,app,cid)['pending_customer_question']
        assert len(gateway.calls)==len(payloads)==1
        assert response['usage']['model_calls']==1


def test_temporary_reason_is_projected_as_freeze(policy_retriever,tmp_path):
    set_policy(policy_retriever,tmp_path,True)
    payloads=[]
    app,gateway=application(policy_retriever,model_fixture(payloads))
    with TestClient(app) as client:
        cid=client.post('/conversations').json()['id']
        review=block_review(client,cid,reason='temporary')
        response=client.post(f'/conversations/{cid}/confirm',json={'review':review['review']}).json()
        assert json.loads(payloads[0]['messages'][0]['content'])['facts']['is_temporary_freeze']
        assert state(client,app,cid)['pending_customer_question'],response


def test_offer_policy_pin_is_rechecked_even_when_not_a_semantic_hit(policy_retriever,tmp_path):
    set_policy(policy_retriever,tmp_path,True)
    evaluator=build_policy_evaluator(retriever=policy_retriever)
    # Model offer was grounded in v1.0.0; current approved source is now v1.0.1.
    # Exclude POL-003 from ordinary replacement retrieval to prove the pin check
    # does not depend on a coincidental semantic result.
    original=policy_retriever.retrieve
    policy_retriever.retrieve=lambda *a,**kw:[e for e in original(*a,**kw) if e.policy_id!='POL-003']
    decision=evaluator.validate_execution('replace_card',{}, {'has_blocked_card':True},
                                           {'pinned_policies':{'POL-003':'1.0.0'}})
    assert not decision.permitted and decision.policy_error=='PolicyRetrievalUnavailable'
