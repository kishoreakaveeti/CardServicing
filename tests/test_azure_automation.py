"""Focused deployment safety tests without Azure accounts or network access."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
INFRA=ROOT/'infra/azure'


def load(name):
    spec=importlib.util.spec_from_file_location('azure_'+name,INFRA/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_heartbeat_preserves_private_capture_and_does_not_resend_stdin(monkeypatch, capsys):
    module=load('validate_local')
    class Process:
        returncode=0
        def __init__(self): self.inputs=[]
        def communicate(self, input=None, timeout=None):
            self.inputs.append(input)
            if len(self.inputs)==1: raise subprocess.TimeoutExpired('docker',15)
            return 'private-captured-result\n',None
        def poll(self): return self.returncode
    process=Process()
    monkeypatch.setattr(module.subprocess,'Popen',lambda *args,**kwargs:process)
    assert module.run_with_progress(['docker'],code='private-stdin',capture=True,label='Docker Compose build')=='private-captured-result'
    assert process.inputs==['private-stdin',None]
    output=capsys.readouterr().out
    assert 'Still running Docker Compose build' in output
    assert 'not provisioned Azure' in output
    assert 'private-captured-result' not in output and 'private-stdin' not in output


def test_preflight_progress_preserves_failure_exit_code(monkeypatch):
    module=load('validate_local')
    class Process:
        returncode=42
        def communicate(self, **kwargs): return None,None
        def poll(self): return 42
    monkeypatch.setattr(module.subprocess,'Popen',lambda *args,**kwargs:Process())
    with pytest.raises(subprocess.CalledProcessError) as failure:
        module.run_with_progress(['docker'],label='Docker build')
    assert failure.value.returncode==42


def test_interrupted_preflight_terminates_its_child(monkeypatch):
    module=load('validate_local')
    class Process:
        returncode=None
        terminated=False
        calls=0
        def communicate(self, **kwargs):
            self.calls+=1
            if self.calls==1: raise KeyboardInterrupt()
            return None,None
        def poll(self): return self.returncode
        def terminate(self): self.terminated=True; self.returncode=-1
    process=Process()
    monkeypatch.setattr(module.subprocess,'Popen',lambda *args,**kwargs:process)
    with pytest.raises(KeyboardInterrupt): module.run_with_progress(['docker'])
    assert process.terminated


def test_dns_rejects_stale_or_conflicting_records():
    module=load('check_dns')
    config={'AppDomain':'bank.owned.test','IdentityDomain':'login.owned.test','PublicIp':'203.0.113.5'}
    def good(*args,**kwargs):
        return [(None,None,None,None,('203.0.113.5',443))]
    module.verify(config,good)
    def wrong(*args,**kwargs):
        return good()+[(None,None,None,None,('2001:db8::1',443))]
    with pytest.raises(RuntimeError,match='DNS'):
        module.verify(config,wrong)


@pytest.mark.parametrize('field,value',[
    ('deployment_id','unrelated'),('version',2),('volumes',['foreign-volume']),('source_sha256','wrong-release')])
def test_restore_rejects_foreign_or_incompatible_backup(field,value):
    module=load('backup_data')
    manifest={'version':1,'deployment_id':'owned','volumes':['owned-volume'],'source_sha256':'abc'}
    module.validate_manifest(manifest,'owned',['owned-volume'],'abc')
    with pytest.raises(ValueError,match='does not match'):
        module.validate_manifest({**manifest,field:value},'owned',['owned-volume'],'abc')


def test_missing_and_malformed_smoke_evidence_fails(tmp_path):
    module=load('report')
    assert all(row['status']=='FAIL' for row in module.collect(tmp_path))
    (tmp_path/'public-report.json').write_text('{"status":"PASS"}')
    assert module.collect(tmp_path)[0]['status']=='FAIL'
    (tmp_path/'public-report.json').write_text(json.dumps([{'check':'tls','status':'PASS','evidence':'trusted certificate'}]))
    results=module.collect(tmp_path)
    assert results[0]['status']=='PASS'
    assert any(row['status']=='FAIL' for row in results[1:])


def powershell():
    exe=shutil.which('pwsh') or shutil.which('powershell')
    if not exe:
        pytest.skip('PowerShell runtime not installed')
    return exe


@pytest.mark.parametrize('scenario', ['default','custom','undersized','missing'])
def test_default_vm_size_skips_catalogue_but_custom_sizes_are_checked(scenario):
    script=r'''
. ./infra/azure/common.ps1
$scenario='SCENARIO'
$script:Cfg=[pscustomobject]@{VmSize=$(if ($scenario -eq 'default') {'Standard_B4ms'} else {'Standard_Custom'});Location='australiaeast'}
$script:calls=0
function Invoke-Az([string[]]$Arguments) {
 $script:calls++
 if ($scenario -eq 'default') {throw 'Default sizing must not query Azure SKUs'}
 if ($scenario -eq 'missing') {return @()}
 $memory=if ($scenario -eq 'undersized') {'8'} else {'16'}
 return [pscustomobject]@{name='Standard_Custom';capabilities=@([pscustomobject]@{name='MemoryGB';value=$memory},[pscustomobject]@{name='vCPUs';value='4'})}
}
$failed=$false
try {Assert-DeploymentVmSize} catch {$failed=$true}
if ($failed -ne ($scenario -in @('undersized','missing'))) {throw 'Incorrect sizing result'}
if ($script:calls -ne $(if ($scenario -eq 'default') {0} else {1})) {throw 'Unexpected SKU queries'}
'''.replace('SCENARIO',scenario)
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True)


def test_azure_progress_keeps_json_results_and_tokens_out_of_console():
    script=r'''
. ./infra/azure/common.ps1
$script:Cfg=[pscustomobject]@{SubscriptionId='11111111-2222-3333-4444-555555555555'}
function Invoke-Native([string]$Program,[string[]]$Arguments) {
 if ($Arguments[0] -eq 'account') {return '{"accessToken":"private-test-token"}'}
 return '{"name":"expected-resource"}'
}
$resource=Invoke-Az @('group','show','-n','rg-test')
if ($resource.name -ne 'expected-resource') {throw 'Progress contaminated JSON return value'}
$token=Invoke-Az @('account','get-access-token')
if ($token.accessToken -ne 'private-test-token') {throw 'Token result altered'}
'''
    result=subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True,text=True)
    assert 'Azure operation completed' in result.stdout
    assert 'private-test-token' not in result.stdout+result.stderr


def test_powershell_entrypoints_parse_without_execution():
    script=r'''
$failures=@()
Get-ChildItem infra/azure/*.ps1,scripts/azure-*.ps1 | ForEach-Object {
    $tokens=$null; $errors=$null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$errors) | Out-Null
    $failures += $errors
}
if ($failures.Count) { $failures | Out-String | Write-Output; exit 1 }
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True)


def test_adapter_scope_is_deterministic_and_rejects_invalid_subscription():
    script=r'''
. ./scripts/azure-common.ps1
$a=Get-AzureConfigPath '11111111-2222-3333-4444-555555555555' 'rg-cardservicing-demo'
$b=Get-AzureConfigPath '11111111-2222-3333-4444-555555555555' 'rg-cardservicing-demo'
$c=Get-AzureConfigPath '11111111-2222-3333-4444-555555555555' 'rg-another-demo'
if ($a -ne $b -or $a -eq $c) { throw 'Unstable or overlapping deployment scope' }
try { Get-AzureConfigPath 'invalid' 'rg-demo'; throw 'Invalid ID accepted' }
catch { if ($_.Exception.Message -ne 'SubscriptionId must be a UUID.') { throw } }
exit 0
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True)


def test_vm_state_polling_and_remainder_discovery():
    script=r'''
. ./infra/azure/common.ps1
$script:Cfg=[pscustomobject]@{SubscriptionId='test';ResourceGroup='owned';VmName='vm';DeploymentId='ours'}
$script:polls=0
function Start-Sleep {}
function Invoke-Az([string[]]$Arguments) {
    if ($Arguments[0] -eq 'vm') {
        $script:polls++
        $state=if ($script:polls -eq 1) { 'PowerState/deallocating' } else { 'PowerState/deallocated' }
        return [pscustomobject]@{instanceView=[pscustomobject]@{statuses=@([pscustomobject]@{code=$state})}}
    }
    return @(
        [pscustomobject]@{name='ours';tags=[pscustomobject]@{cardservicingDeployment='ours'}}
        [pscustomobject]@{name='legacy';tags=[pscustomobject]@{application='cardservicing-demo'}}
        [pscustomobject]@{name='foreign';tags=[pscustomobject]@{cardservicingDeployment='foreign'}}
        [pscustomobject]@{name='untagged';tags=$null}
    )
}
Wait-VmState 'deallocated'
if ($script:polls -ne 2) { throw 'Deallocation was not polled' }
$found=@(Get-DeploymentRemainders)
if ($found.Count -ne 2 -or $found.name -contains 'foreign') { throw 'Incorrect ownership discovery' }
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True)


def test_unattended_ssh_key_generation(tmp_path):
    if not shutil.which('ssh-keygen'):
        pytest.skip('OpenSSH not installed')
    key=str(tmp_path/'deployment-key').replace("'","''")
    script=f'''
. ./infra/azure/common.ps1
$emptyPassphrase = if ($PSVersionTable.PSVersion.Major -le 5) {{ '""' }} else {{ '' }}
Invoke-Native 'ssh-keygen' @('-q','-t','ed25519','-f','{key}','-N',$emptyPassphrase) | Out-Null
Invoke-Native 'ssh-keygen' @('-y','-f','{key}') | Out-Null
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True,timeout=30)
    assert (tmp_path/'deployment-key.pub').is_file()


@pytest.mark.parametrize('scenario', ['delete','absent','whatif','wrong-confirmation','remaining','deletion-failure'])
def test_destroy_safety_with_mocked_azure(tmp_path, scenario):
    # Execute the real destroy entrypoint, substituting only its Azure/IO helpers.
    shutil.copy(INFRA/'destroy.ps1', tmp_path/'destroy.ps1')
    (tmp_path/'common.ps1').write_text(r'''
$ErrorActionPreference='Stop'
$script:calls=@()
function Read-Deployment($path) {
    $script:Cfg=[pscustomobject]@{SubscriptionId='test';ResourceGroup='rg-owned';DeploymentId='owned'}
    return $script:Cfg
}
function Assert-Tools {}
function Assert-Login {}
function Assert-ExpectedResources {}
function Get-OwnedGroup { if ($env:AZURE_TEST_SCENARIO -ne 'absent') { return @{tags=@{cardservicingDeployment='owned'}} } }
function Get-DeploymentRemainders {
    if ($env:AZURE_TEST_SCENARIO -eq 'remaining') { return [pscustomobject]@{name='orphan';type='disk';resourceGroup='outside';id='orphan'} }
}
function Read-Host { if ($env:AZURE_TEST_SCENARIO -eq 'wrong-confirmation') { return 'wrong' }; return 'rg-owned' }
function Invoke-Az([string[]]$Arguments) {
    $script:calls += ($Arguments -join ' ')
    if ($Arguments[1] -eq 'delete' -and $env:AZURE_TEST_SCENARIO -eq 'deletion-failure') { throw 'Azure rejected deletion' }
    if ($Arguments[1] -eq 'exists') { return $false }
    if ($Arguments[1] -eq 'list') { return [pscustomobject]@{name='vm';type='vm';id='vm'} }
}
''')
    import os
    env={**os.environ,'AZURE_TEST_SCENARIO':scenario}
    escaped=str(tmp_path/'destroy.ps1').replace("'","''")
    script=f'''
$failed=$false
try {{ . '{escaped}' -ConfigPath unused -WhatIf:${str(scenario=='whatif').lower()} }} catch {{ $failed=$true }}
$expectedFailure=$env:AZURE_TEST_SCENARIO -in @('wrong-confirmation','remaining','deletion-failure')
if ($failed -ne $expectedFailure) {{ throw 'Unexpected failure result' }}
$deleted=@($script:calls | Where-Object {{ $_ -like 'group delete *' }}).Count -gt 0
if ($deleted -ne ($env:AZURE_TEST_SCENARIO -in @('delete','remaining','deletion-failure'))) {{ throw 'Unsafe deletion behavior' }}
if ($env:AZURE_TEST_SCENARIO -eq 'delete' -and -not ($script:calls -match 'group exists')) {{ throw 'Deletion not verified' }}
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],env=env,cwd=ROOT,check=True,capture_output=True)


def test_foreign_resource_group_and_unexpected_resources_are_refused():
    script=r'''
. ./infra/azure/common.ps1
$script:Cfg=[pscustomobject]@{ResourceGroup='owned';DeploymentId='expected';VmName='vm-demo';StorageAccount='storageowned'}
function Invoke-Az([string[]]$Arguments) {
    if ($Arguments[1] -eq 'exists') { return $true }
    return [pscustomobject]@{tags=[pscustomobject]@{cardservicingDeployment='foreign'}}
}
$refused=$false
try { Get-OwnedGroup | Out-Null } catch { $refused=$true }
if (-not $refused) { throw 'Foreign ownership was accepted' }
function Invoke-Az([string[]]$Arguments) {
    return @([pscustomobject]@{type='Microsoft.Compute/virtualMachines';name='unrelated-vm'})
}
$refused=$false
try { Assert-ExpectedResources } catch { $refused=$true }
if (-not $refused) { throw 'Unrelated resource was accepted' }
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True)


def test_fincrime_model_initialization_fails_closed(monkeypatch):
    from fastapi.testclient import TestClient
    import card_chat.fincrime_agent as module
    def broken(config):
        raise RuntimeError('Model configuration missing')
    monkeypatch.setattr(module,'make_model',broken)
    with pytest.raises(RuntimeError,match='Model configuration'):
        with TestClient(module.create_app(config={'TELEMETRY_SINK':'none'})):
            pass


def test_fincrime_security_initialization_fails_closed(monkeypatch):
    import card_chat.fincrime_agent as module
    def broken(*args,**kwargs):
        raise ValueError('Invalid security configuration')
    monkeypatch.setattr(module,'protect',broken)
    with pytest.raises(ValueError,match='security configuration'):
        module.create_app(config={'OIDC_ISSUER':'https://identity.owned.test/realms/cards'})


def test_config_validation_accepts_safe_values_and_rejects_broad_ssh(tmp_path):
    config=json.loads((INFRA/'deployment.example.json').read_text())
    config.update(SubscriptionId='11111111-2222-3333-4444-555555555555',DeploymentId='validation-config-test',
        StorageAccount='validationstorageonly',AppDomain='bank.owned-domain.net',IdentityDomain='login.owned-domain.net',
        AcmeEmail='operator@owned-domain.net',SshSourceCidr='203.0.113.4/32')
    path=tmp_path/'config.json'
    path.write_text(json.dumps(config))
    escaped=str(path).replace("'","''")
    script=f'''
. ./infra/azure/common.ps1
$c=Read-Deployment '{escaped}'
if ($c.Location -ne 'australiaeast') {{ throw 'Wrong location' }}
Remove-Item -LiteralPath $script:StateDir
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True)
    config['SshSourceCidr']='0.0.0.0/0'
    path.write_text(json.dumps(config))
    script=f'''
. ./infra/azure/common.ps1
$refused=$false
try {{ Read-Deployment '{escaped}' | Out-Null }} catch {{ $refused=$true }}
if (-not $refused) {{ throw 'Unrestricted SSH was accepted' }}
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True)
