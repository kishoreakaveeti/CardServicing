[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath, [switch]$RestartContainers)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
if (-not (Get-OwnedGroup)) { throw 'Deployment does not exist.' }
Assert-OwnedVm
Invoke-Az -Arguments @('vm','start','-g',$c.ResourceGroup,'-n',$c.VmName) | Out-Null
Wait-VmState 'running'
$address = Get-VmAddress
Set-TrustedHost $address
Wait-Ssh $address
$action = if ($RestartContainers) { 'restart' } else { 'start' }
Invoke-Remote $address ("sudo systemctl start docker && sudo systemctl start cardservicing && sudo bash /opt/cardservicing/app/infra/azure/control.sh " + $action)
Invoke-Remote $address 'sudo bash /opt/cardservicing/app/infra/azure/control.sh verify-resume' -Stream
$resume = Invoke-Remote $address 'sudo cat /opt/cardservicing/state/restart-report.json'
[IO.File]::WriteAllText((Join-Path $script:StateDir 'resume-report.json'), ($resume -join "`n"), [Text.UTF8Encoding]::new($false))
& "$PSScriptRoot/smoke-test.ps1" -ConfigPath $ConfigPath
Write-Host "Application: https://$($c.AppDomain)"
Write-CostReminder
