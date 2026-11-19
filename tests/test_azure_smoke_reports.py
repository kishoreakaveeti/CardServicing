"""Smoke reporting contracts without Azure/model requests or banking writes."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(name):
    spec=importlib.util.spec_from_file_location('smoke_'+name,ROOT/'infra/azure'/f'{name}.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('scenario',['multiple','single','empty','wrapped','missing-status','bad-status'])
def test_windows_powershell_report_rows_are_flat_and_validated(scenario):
    exe=shutil.which('pwsh') or shutil.which('powershell')
    if not exe:
        pytest.skip('PowerShell unavailable')
    rows=[{'check':'tls','status':'PASS','evidence':'trusted'},{'check':'login','status':'FAIL','evidence':'failed'}]
    data={'multiple':rows,'single':rows[:1],'empty':[],
          'wrapped':[{'value':rows,'Count':2}],
          'missing-status':[{'check':'bad','evidence':'missing'}],
          'bad-status':[{'check':'bad','evidence':'invalid','status':'UNKNOWN'}]}[scenario]
    payload=json.dumps(data).replace("'","''")
    script=f'''
. ./infra/azure/common.ps1
$failed=$false
try {{ $rows=@(ConvertFrom-DeploymentSmokeReport '{payload}') }} catch {{$failed=$true}}
if ($failed -ne ${str(scenario not in ('multiple','single')).lower()}) {{throw 'Invalid schema accepted or good report refused'}}
if (-not $failed) {{
 $combined=$rows+@([pscustomobject]@{{check='network';status='PASS';evidence='private'}})
 $expected={3 if scenario=='multiple' else 2}
 if ($combined.Count -ne $expected) {{throw 'Nested report array'}}
 $failures=@($combined | Where-Object {{ $_.status -eq 'FAIL' }})
 if ($failures.Count -ne {1 if scenario=='multiple' else 0}) {{throw 'Failed rows were hidden'}}
 $roundtrip=ConvertFrom-Json -InputObject (ConvertTo-Json -InputObject $combined -Depth 8)
 if (@($roundtrip).Count -ne $expected) {{throw 'Report serialization nested rows'}}
}}
exit 0
'''
    result=subprocess.run([exe,'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr


def test_oidc_login_client_is_entered_once_and_closed(monkeypatch):
    module=load('smoke')
    original=httpx.Client
    def endpoint(request):
        if request.url.path=='/auth/login':
            return httpx.Response(200,text='<form id="kc-form-login" action="https://app.test/realms/cards/login"><input type="hidden" name="state" value="fake"></form>')
        if request.url.path=='/realms/cards/login':
            return httpx.Response(200,headers={'set-cookie':'card_session=fake; Path=/; Secure; HttpOnly'})
        return httpx.Response(200,json={'csrf':'fake'})
    def client(**kwargs):
        return original(transport=httpx.MockTransport(endpoint),**kwargs)
    monkeypatch.setattr(module.httpx,'Client',client)
    with module.sign_in_oidc('https://app.test','https://app.test','fake-password') as authenticated:
        assert authenticated.get('/api/session').status_code==200
    assert authenticated.is_closed


@pytest.mark.parametrize('increment,expected,passes',[(1,1,True),(2,2,True),(1,2,False),(0,1,False)])
def test_smoke_counts_new_actions_instead_of_cumulative_success(monkeypatch,increment,expected,passes):
    module=load('smoke')
    # Prior create/block/unblock actions must not conceal a missing replacement.
    review={'review':'synthetic-review','can_confirm':True,'usage':{'completed_actions':5}}
    result={'usage':{'completed_actions':5+increment}}
    calls=[]
    def operation(client,path,body,key=None):
        calls.append((path,key))
        return review if path.endswith('/inputs') else result
    monkeypatch.setattr(module,'operation',operation)
    if passes:
        assert module.execute(None,'/synthetic','block_card',{},expected_actions=expected)==result
        assert calls[1][1]==calls[2][1]
    else:
        with pytest.raises(AssertionError):
            module.execute(None,'/synthetic','block_card',{},expected_actions=expected)


@pytest.mark.parametrize('missing_replacement',[False,True])
def test_public_smoke_replacement_supplies_country_and_verifies_actual_card_states(tmp_path,monkeypatch,missing_replacement):
    module=load('smoke')
    monkeypatch.setattr(module,'STATE',tmp_path)
    (tmp_path/'deployment.json').write_text(json.dumps({'AppDomain':'app.test','IdentityDomain':'app.test'}))
    (tmp_path/'.env.stack').write_text('DEMO_PASSWORD=synthetic-password')
    counts={'actions':0,'active':0,'blocked':0}
    prompts=[]
    def options():
        return {'tools':[{'name':name,'fields':[{'name':'card_id','options':[
            {'ref':name+'-synthetic','label':label} for _ in range(count)]}]}
            for name,count,label in [('block_card',counts['active'],'Synthetic active card'),
                ('unblock_card',counts['blocked'],'Synthetic blocked card'),
                ('replace_card',counts['blocked'],'Synthetic blocked card')]]}
    class Client:
        def __init__(self,**kwargs): self.headers={}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,path):
            if path.endswith('/health'): return httpx.Response(200)
            if 'openid-configuration' in path: return httpx.Response(200,json={'issuer':'https://app.test/realms/cards'})
            return httpx.Response(200,json=options())
        def post(self,path,**kwargs):
            return httpx.Response(401 if path=='/api/conversations' else 201,json={'csrf':'synthetic'})
    monkeypatch.setattr(module.httpx,'Client',Client)
    from contextlib import contextmanager
    @contextmanager
    def login(*args):
        class Login:
            def get(self,*args): return httpx.Response(200)
            def post(self,*args,**kwargs): return httpx.Response(403)
        yield Login()
    monkeypatch.setattr(module,'sign_in_oidc',login)
    def operation(client,path,body,key=None):
        if path=='/api/conversations': return {'id':'synthetic'}
        prompts.append(body['text'])
        name='unblock_card' if 'unfreeze' in body['text'] else 'block_card'
        return {'input_tool':name,'usage':{'model_calls':1}}
    monkeypatch.setattr(module,'operation',operation)
    def execute(client,path,tool,fields,selections=None,*,expected_actions=1):
        if tool=='create_account': counts.update(active=1)
        elif tool=='unblock_card': counts.update(active=1,blocked=0)
        elif expected_actions==2:
            assert 'I am currently in Australia' in prompts[-1]
            counts.update(active=0 if missing_replacement else 1,blocked=1)
        else: counts.update(active=0,blocked=1)
        counts['actions']+=expected_actions
        return {'usage':{'completed_actions':counts['actions']}}
    monkeypatch.setattr(module,'execute',execute)
    module.main()
    report=json.loads((tmp_path/'public-report.json').read_text())
    if missing_replacement:
        assert any(row['check']=='public-banking-workflows' and row['status']=='FAIL'
                   and row['evidence']=='card-replacement: AssertionError' for row in report),report
        assert not any(row['check']=='card-replacement' and row['status']=='PASS' for row in report)
        assert not (tmp_path/'smoke-customer.json').exists()
    else:
        assert not any(row['status']=='FAIL' for row in report),report
        assert any(row['check']=='card-replacement' and row['status']=='PASS' for row in report)
        assert json.loads((tmp_path/'smoke-customer.json').read_text())['snapshot']


@pytest.mark.parametrize('published',['127.0.0.1','0.0.0.0'])
def test_isolation_counts_configured_services_even_with_caddy_in_same_project(tmp_path,monkeypatch,published):
    module=load('verify_isolation')
    config={'services':{'frontend':{},'cards-api':{'environment':{'TELEMETRY_SINK':'azure_blob'}}}}
    def output(command,**kwargs):
        if 'config' in command:
            return json.dumps(config)
        if 'ps' in command:
            assert command[-2:]==['frontend','cards-api'], 'Unfiltered compose ps includes the host-network Caddy service'
            return 'frontend-id\napi-id\n'
        return json.dumps([{'NetworkSettings':{'Ports':{'8080/tcp':[{'HostIp':published}]}}},
                           {'NetworkSettings':{'Ports':{'8000/tcp':None}}}])
    monkeypatch.setattr(module.subprocess,'check_output',output)
    module.main(tmp_path)
    result=json.loads((tmp_path/'isolation-report.json').read_text())
    assert result[0]['status']==('PASS' if published=='127.0.0.1' else 'FAIL')
