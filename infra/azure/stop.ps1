[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath, [switch]$ServicesOnly)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
if (-not (Get-OwnedGroup)) { throw 'Deployment does not exist.' }
$view = Invoke-Az -Arguments @('vm','get-instance-view','-g',$c.ResourceGroup,'-n',$c.VmName)
if ($view.instanceView.statuses.code -contains 'PowerState/running') {
    $address = Get-VmAddress
    Invoke-Remote $address 'sudo systemctl stop cardservicing; sudo bash /opt/cardservicing/app/infra/azure/control.sh stop'
}
if (-not $ServicesOnly) {
    Invoke-Az -Arguments @('vm','deallocate','-g',$c.ResourceGroup,'-n',$c.VmName) | Out-Null
    Write-Host 'VM deallocated. Persistent OS disk, Docker volumes and deployment state retained.'
} else { Write-Host 'Services stopped. The VM is still allocated and compute charges continue.' }
Write-CostReminder
