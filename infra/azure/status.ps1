[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
if (-not (Get-OwnedGroup)) { Write-Host 'Dedicated resource group does not exist.'; return }
$resources = @(Invoke-Az -Arguments @('resource','list','-g',$c.ResourceGroup))
$resources | Select-Object name,type,location | Format-Table
$view = Invoke-Az -Arguments @('vm','get-instance-view','-g',$c.ResourceGroup,'-n',$c.VmName)
$view.instanceView.statuses | Select-Object code,displayStatus | Format-Table
if ($view.instanceView.statuses.code -contains 'PowerState/running') {
    Invoke-Remote (Get-VmAddress) 'sudo bash /opt/cardservicing/app/infra/azure/control.sh status'
}
Write-CostReminder
