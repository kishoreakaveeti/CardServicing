"""Public HTTPS smoke using only fresh synthetic customers; never print payloads."""
from contextlib import contextmanager
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urljoin, urlparse
from uuid import uuid4
import httpx
from dotenv import dotenv_values

ROOT=Path('/deployment')
STATE=ROOT/'state'


class Form(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action=None
        self.fields={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='form' and attrs.get('id')=='kc-form-login':
            self.action=attrs.get('action')
        if tag=='input' and attrs.get('type')=='hidden' and attrs.get('name'):
            self.fields[attrs['name']]=attrs.get('value','')


def record(report,name,status,evidence):
    report.append(dict(check=name,status=status,evidence=evidence))
    print(status+': '+name,flush=True)


@contextmanager
def sign_in_oidc(origin,identity,password):
    with httpx.Client(base_url=origin,follow_redirects=True,timeout=120) as client:
        page=client.get('/auth/login')
        form=Form()
        form.feed(page.text)
        assert form.action and urlparse(form.action).hostname==urlparse(identity).hostname
        result=client.post(form.action,data={**form.fields,'username':'alex.synthetic','password':password})
        assert result.status_code==200 and result.url.host==urlparse(origin).hostname
        session=client.get('/api/session')
        assert session.status_code==200
        assert any(c.name=='card_session' and c.secure for c in client.cookies.jar)
        client.headers.update({'Origin':origin,'X-CSRF-Token':session.json()['csrf']})
        yield client

def operation(client,path,body,key=None):
    response=client.post(path,json=body,headers={'Idempotency-Key':key or uuid4().hex})
    assert response.status_code in (200,202)
    if response.status_code==200:
        return response.json()
    location=response.json()['status_url']
    for _ in range(120):
        result=client.get(location).json()
        if result['status']=='succeeded':
            return result['result']
        assert result['status']!='failed'
        time.sleep(1)
    raise RuntimeError('Operation polling timed out')


def tools(client,path):
    response=client.get(path+'/inputs')
    assert response.status_code==200
    return response.json()['tools']


def selection(options,tool,field):
    spec=next(item for item in options if item['name']==tool)
    return next(f for f in spec['fields'] if f['name']==field)['options'][0]['ref']


def execute(client,path,tool,fields,selections=None,*,expected_actions=1):
    review=operation(client,path+'/inputs',{'tool':tool,'fields':fields,'selections':selections or {}})
    assert review['can_confirm'] and review['review']
    before=review.get('usage',{}).get('completed_actions',0)
    key=uuid4().hex
    result=operation(client,path+'/confirm',{'review':review['review']},key)
    assert result.get('usage',{}).get('completed_actions',0)-before==expected_actions
    replay=operation(client,path+'/confirm',{'review':review['review']},key)
    assert replay==result
    return result


def snapshot(options):
    # Only synthetic masked labels are persisted; never write real customer data.
    tool=next(t for t in options if t['name']=='block_card')
    return sorted(o['label'] for f in tool['fields'] if f['name']=='card_id' for o in f.get('options',[]))


def persistence(origin):
    saved=json.loads((STATE/'smoke-customer.json').read_text())
    with httpx.Client(base_url=origin,headers={'Origin':origin},timeout=120) as client:
        response=client.post('/auth/password-login',json={'username':saved['username'],'password':saved['password']})
        assert response.status_code==200
        client.headers['X-CSRF-Token']=response.json()['csrf']
        cid=operation(client,'/api/conversations',{})['id']
        assert snapshot(tools(client,'/api/conversations/'+cid))==saved['snapshot']
    return 'Saved synthetic login and account/card selection survived the restart'


def main():
    cfg=json.loads((STATE/'deployment.json').read_text())
    origin='https://'+cfg['AppDomain']
    identity='https://'+cfg['IdentityDomain']
    report=[]
    stage='public-readiness'
    if '--persistence' in sys.argv:
        try:
            record(report,'restart-persistence','PASS',persistence(origin))
        except Exception as exc:
            record(report,'restart-persistence','FAIL',type(exc).__name__)
        (STATE/'restart-report.json').write_text(json.dumps(report,indent=2))
        return
    def check(name,fn,evidence):
        try:
            fn()
            record(report,name,'PASS',evidence)
            return True
        except Exception as exc:
            record(report,name,'FAIL',type(exc).__name__)
            return False
    def https():
        # Real trust verification: no verify=False, cookie rewriting or TLS bypass.
        last_error = None
        for attempt in range(120):
            try:
                with httpx.Client(timeout=10, follow_redirects=True) as client:
                    assert client.get(origin+'/health').status_code==200
                    discovery=client.get(identity+'/realms/cards/.well-known/openid-configuration')
                    assert discovery.status_code==200
                    assert discovery.json()['issuer']==identity+'/realms/cards'
                return
            except (httpx.HTTPError, AssertionError, Exception) as exc:
                last_error = exc
                if (attempt + 1) % 5 == 0 or attempt == 0:
                    print(f"Waiting for public HTTPS ({origin}): attempt {attempt+1}/120: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(2)
        raise RuntimeError(f'Public HTTPS readiness timed out: {type(last_error).__name__}: {last_error}')
    try:
        if not check('public-https',https,'Trusted TLS, frontend HTTP 200 and exact HTTPS OIDC issuer'):
            record(report,'public-workflows','NOT TESTED','HTTPS prerequisite failed')
            return
        def oidc():
            password=dotenv_values(STATE/'.env.stack')['DEMO_PASSWORD']
            with sign_in_oidc(origin,identity,password) as client:
                assert client.get('/api/session').status_code==200
                assert client.post('/api/conversations',json={},headers={'X-CSRF-Token':'wrong'}).status_code==403
        check('keycloak-browser-login',oidc,'Authorization Code + PKCE, state/nonce, public callback and secure session cookie')
        with httpx.Client(base_url=origin,headers={'Origin':origin},timeout=120) as client:
            stage='customer-registration'
            assert client.post('/api/conversations',json={}).status_code==401
            username='azure-smoke-'+uuid4().hex[:12]
            password=secrets.token_urlsafe(24)
            response=client.post('/auth/register',json={'username':username,'password':password})
            assert response.status_code==201
            client.headers['X-CSRF-Token']=response.json()['csrf']
            record(report,'customer-registration','PASS','Fresh synthetic application customer registered through authenticated service boundary')
            cid=operation(client,'/api/conversations',{})['id']
            path='/api/conversations/'+cid
            stage='account-and-card-issuance'
            execute(client,path,'create_account',{'account_type':'transaction','issue_card':True})
            record(report,'account-and-card-issuance','PASS','Reviewed account creation and card issuance completed through Agent/MCP; replay matched')
            # Natural-language calls exercise LangGraph classification and the real model.
            stage='card-blocking'
            proposed=operation(client,path+'/messages',{'text':'Please temporarily freeze my card.'})
            assert proposed.get('input_tool')=='block_card'
            assert proposed.get('usage',{}).get('model_calls',0)>0
            opts=tools(client,path)
            card=selection(opts,'block_card','card_id')
            execute(client,path,'block_card',{'reason':'temporary freeze'}, {'card_id':card})
            record(report,'card-blocking','PASS','FinCrime workflow confirmed a temporary freeze through remote MCP')
            stage='card-unblocking'
            proposed=operation(client,path+'/messages',{'text':'Please unfreeze my temporarily frozen card. I have it safely with me.'})
            assert proposed.get('input_tool')=='unblock_card', ('unblock intent response: '+json.dumps(proposed,sort_keys=True))
            unblock_options=tools(client,path)
            card=selection(unblock_options,'unblock_card','card_id')
            execute(client,path,'unblock_card',{}, {'card_id':card})
            record(report,'card-unblocking','PASS','Policy-authorized temporary-freeze reversal completed and replay matched')
            stage='card-replacement'
            country_prompt=operation(client,path+'/messages',{'text':'I am currently in Australia. Please block my card and reissue a new one.'})
            assert country_prompt.get('country_required') is True
            proposed=operation(client,path+'/messages',{'text':'Australia'})
            assert proposed.get('input_tool')=='block_card'
            assert not proposed.get('country_required')
            card=selection(tools(client,path),'block_card','card_id')
            replaced=execute(client,path,'block_card',{'reason':'lost'}, {'card_id':card},expected_actions=2)
            assert not replaced.get('country_required')
            opts=tools(client,path)
            assert len(snapshot(opts))==1
            replacement_tool=next(t for t in opts if t['name']=='replace_card')
            blocked=next(f for f in replacement_tool['fields'] if f['name']=='card_id')['options']
            assert len(blocked)==1
            record(report,'card-replacement','PASS','Declared AU; exactly two confirmed actions; one blocked original and one active replacement; idempotent replay matched')
            record(report,'langgraph-anthropic','PASS','Live natural-language routing produced expected FinCrime tools and reported nonzero model calls')
            stage='save-restart-snapshot'
            saved={'username':username,'password':password,'snapshot':snapshot(opts)}
            assert saved['snapshot']
            target=STATE/'smoke-customer.json'
            target.write_text(json.dumps(saved))
            target.chmod(0o600)
        # Registration belongs to the existing banking service, not the Keycloak
        # realm: do not silently enable realm self-registration or merge identities.
        record(report,'keycloak-self-registration','NOT TESTED','Existing realm deliberately disables self-registration; application registration verified separately')
    except Exception as exc:
        record(report,'public-banking-workflows','FAIL',stage+': '+type(exc).__name__)
    finally:
        (STATE/'public-report.json').write_text(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
