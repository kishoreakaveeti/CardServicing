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


def test_powershell_entrypoints_parse_without_execution():
    script=r'''
$failures=@()
Get-ChildItem infra/azure/*.ps1 | ForEach-Object {
    $tokens=$null; $errors=$null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$errors) | Out-Null
    $failures += $errors
}
if ($failures.Count) { $failures | Out-String | Write-Output; exit 1 }
'''
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,check=True,capture_output=True)


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
