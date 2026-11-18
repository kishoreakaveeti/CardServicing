"""Deployment setup prompts with fake keys and mocked public-IP services only."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_ps(script):
    exe = shutil.which('pwsh') or shutil.which('powershell')
    if not exe:
        pytest.skip('PowerShell unavailable')
    result = subprocess.run([exe, '-NoProfile', '-NonInteractive', '-Command',
                             '. ./scripts/azure-common.ps1\n' + script],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def test_private_acl_only_updates_access_permissions(tmp_path):
    path = str(tmp_path/'private').replace("'", "''")
    run_ps(r'''
if ($env:OS -ne 'Windows_NT') {exit 0}
$path='TEST_PATH'
New-Item -ItemType Directory $path | Out-Null
$file=Join-Path $path 'key.txt'
[IO.File]::WriteAllText($file,'fake-key')
function Set-Acl {throw 'Set-Acl must not be called (SeSecurityPrivilege regression)'}
foreach ($target in @($path,$file)) {
 $before=Get-Acl -LiteralPath $target
 Set-PrivateWindowsAccess $target
 Set-PrivateWindowsAccess $target
 $after=Get-Acl -LiteralPath $target
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 $rules=@($after.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
 if ($before.Owner -ne $after.Owner -or -not $after.AreAccessRulesProtected -or $rules.Count -ne 1 -or $rules[0].IdentityReference.Value -ne $sid) {throw 'Owner changed or current-user-only ACL was not applied'}
}
'''.replace('TEST_PATH', path))


def test_create_reuses_legacy_state_without_budget_parameters(tmp_path):
    import json
    scripts = tmp_path/'scripts'
    infra = tmp_path/'infra'/'azure'
    scripts.mkdir()
    infra.mkdir(parents=True)
    shutil.copy(ROOT/'scripts/azure-create.ps1', scripts/'azure-create.ps1')
    key = tmp_path/'ssh-key'
    key.write_text('fake-local-key')
    config = tmp_path/'deployment.json'
    config.write_text(json.dumps({
        'SubscriptionId':'11111111-2222-3333-4444-555555555555',
        'ResourceGroup':'rg-cardservicing-demo','VmName':'vm-cardservicing-demo',
        'Location':'australiaeast','DnsLabel':'cardservicing-test',
        'AppDomain':'old.test','IdentityDomain':'old.test',
        'AnthropicKeyFile':str(tmp_path/'fake-api-key'),'SshPrivateKey':str(key),
        'CostControl':{'Amount':10,'Currency':'AUD','Email':'old@owned.test'},
    }))
    path = str(config).replace("'", "''")
    (scripts/'azure-common.ps1').write_text("$script:fixturePath='" + path + "'\n" + r'''
$ErrorActionPreference='Stop'
function Get-Command {return [pscustomobject]@{Name='mock'}}
function Write-DeploymentStage($Message) {}
function Get-AzureConfigPath {return $script:fixturePath}
function Read-AzureConfigPath {return $script:fixturePath}
function Read-Deployment($Path) {return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json}
function Initialize-AzureAnthropicKey($Path) {}
''')
    # A strict parameter list fails if the cancelled budget parameter is still passed.
    (infra/'deploy.ps1').write_text(r'''
[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath)
[IO.File]::WriteAllText(($ConfigPath + '.reached'),'app-deployment')
''')
    path = str(config).replace("'", "''")
    entry = str(scripts/'azure-create.ps1').replace("'", "''")
    run_ps(f'''
$script:fixturePath='{path}'
& '{entry}' -SubscriptionId '11111111-2222-3333-4444-555555555555'
''')
    assert (tmp_path/'deployment.json.reached').read_text() == 'app-deployment'
    assert 'CostControl' not in json.loads(config.read_text())


@pytest.mark.parametrize('scenario', ['matching', 'conflicting', 'unavailable', 'private', 'ipv6'])
def test_public_ip_detection_and_fallback(scenario):
    run_ps(r'''
$scenario='SCENARIO'
$script:lookups=0
$script:prompts=0
function Invoke-RestMethod {
 param($Uri,$TimeoutSec,$ErrorAction)
 $script:lookups++
 if ($TimeoutSec -ne 10 -or $Uri -notlike 'https://*') {throw 'Unsafe lookup'}
 switch ($scenario) {
  unavailable {throw 'Mock network failure'}
  private {return '192.168.1.1'}
  ipv6 {return '2001:db8::1'}
  conflicting {if ($script:lookups -eq 2) {return '1.1.1.1'}}
 }
 return "8.8.8.8`n"
}
function Read-Host {$script:prompts++; return '198.51.100.10/32'}
$cidr=Get-AzureSshSourceCidr
if ($script:lookups -ne 2) {throw 'Detection did not cross-check both services'}
if ($scenario -eq 'matching') {
 if ($cidr -ne '8.8.8.8/32' -or $script:prompts) {throw 'Unnecessary CIDR prompt'}
} elseif ($cidr -ne '198.51.100.10/32' -or $script:prompts -ne 1) {throw 'Unreliable detection did not fall back safely'}
'''.replace('SCENARIO', scenario))


@pytest.mark.parametrize('scenario', ['missing', 'existing', 'invalid'])
def test_secure_key_prompt_and_reuse(tmp_path, scenario):
    path = str(tmp_path/'secrets'/'key.txt').replace("'", "''")
    output = run_ps(r'''
$path='KEY_PATH'
$scenario='SCENARIO'
$script:prompts=0
$fakeKey='test-only-not-a-real-api-key-123456789'
if ($scenario -eq 'existing') {
 New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
 [IO.File]::WriteAllText($path,'existing-key-do-not-overwrite')
}
function Read-Host {
 param($Prompt,[switch]$AsSecureString)
 if (-not $AsSecureString) {throw 'Key prompt was not masked'}
 $script:prompts++
 $value=if ($scenario -eq 'invalid') {'bad'} else {$fakeKey}
 return ConvertTo-SecureString $value -AsPlainText -Force
}
$failed=$false
try {Initialize-AzureAnthropicKey $path} catch {$failed=$true}
if ($failed -ne ($scenario -eq 'invalid')) {throw 'Unexpected key setup result'}
if ($scenario -eq 'existing') {
 if ($script:prompts -or [IO.File]::ReadAllText($path) -ne 'existing-key-do-not-overwrite') {throw 'Existing key overwritten or prompted'}
} elseif ($scenario -eq 'invalid') {
 if (Test-Path -LiteralPath $path) {throw 'Invalid key was persisted'}
} else {
 if ($script:prompts -ne 1 -or [IO.File]::ReadAllText($path) -ne $fakeKey) {throw 'Key not provisioned'}
 if ($env:OS -eq 'Windows_NT') {
  $acl=Get-Acl -LiteralPath $path
  $owner=[Security.Principal.WindowsIdentity]::GetCurrent().Name
  if (-not $acl.AreAccessRulesProtected -or @($acl.Access).Count -ne 1 -or $acl.Access[0].IdentityReference.Value -ne $owner) {throw 'Key ACL is not private'}
 }
 if (@(Get-ChildItem -Force (Split-Path $path) -Filter '.cardservicing-key-*').Count) {throw 'Staging files retained'}
}
exit 0
'''.replace('KEY_PATH', path).replace('SCENARIO', scenario))
    assert 'test-only-not-a-real-api-key-123456789' not in output
