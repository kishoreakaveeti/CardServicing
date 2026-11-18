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
