[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath, [switch]$RestartContainers)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
if (-not (Get-OwnedGroup)) { throw 'Deployment does not exist.' }
Invoke-Az -Arguments @('vm','start','-g',$c.ResourceGroup,'-n',$c.VmName) | Out-Null
$address = Get-VmAddress
Set-TrustedHost $address
Wait-Ssh $address
$action = if ($RestartContainers) { 'restart' } else { 'start' }
Invoke-Remote $address ("sudo systemctl start cardservicing; sudo bash /opt/cardservicing/app/infra/azure/control.sh " + $action)
Write-Host "Application: https://$($c.AppDomain)"
Write-CostReminder
