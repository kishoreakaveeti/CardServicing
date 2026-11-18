[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath,
      [Parameter(Mandatory)][string]$ConfirmDestroyDeployment,
      [Parameter(Mandatory)][switch]$DeletePersistentData)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
if ($ConfirmDestroyDeployment -cne $c.DeploymentId -or -not $DeletePersistentData) {
    throw 'Destruction requires the exact DeploymentId and -DeletePersistentData. Back up first.'
}
if (-not (Get-OwnedGroup)) { Write-Host 'Dedicated resource group is already absent.'; return }
Assert-ExpectedResources
$resources = @(Invoke-Az -Arguments @('resource','list','-g',$c.ResourceGroup))
$resources | Select-Object name,type | Format-Table
Write-Host 'Deleting this owned demo resource group, including its persistent disks and Blob telemetry.'
Invoke-Az -Arguments @('group','delete','-n',$c.ResourceGroup,'--yes') | Out-Null
Write-Host 'Dedicated group deleted. Local encrypted backups and unrelated resources were retained.'
