[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory)][string]$ConfigPath,
      [string]$ConfirmDestroyDeployment, [switch]$DeletePersistentData)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
$group = Get-OwnedGroup
if ($group) {
    Assert-ExpectedResources
    @(Invoke-Az @('resource','list','-g',$c.ResourceGroup)) | Select-Object name,type,id | Format-Table -AutoSize
    Write-Warning 'PERMANENT DATA LOSS: disks, Docker volumes, databases, identity, Qdrant and Blob telemetry will be deleted. Download an encrypted backup first if needed.'
    if ($PSCmdlet.ShouldProcess("$($c.SubscriptionId)/$($c.ResourceGroup)", 'Permanently delete all resources and persistent data')) {
        $typed = Read-Host 'Type the exact resource group name to permanently delete it'
        if ($typed -cne $c.ResourceGroup) { throw 'Confirmation did not match; nothing deleted.' }
        Invoke-Az @('group','delete','-n',$c.ResourceGroup,'--yes') | Out-Null
        Invoke-Az @('group','wait','-n',$c.ResourceGroup,'--deleted','--interval','10','--timeout','1800') | Out-Null
        if (Invoke-Az @('group','exists','-n',$c.ResourceGroup)) { throw 'Resource group still exists.' }
        Write-Host 'Verified resource group deletion.'
    }
} else { Write-Host 'Dedicated resource group is already absent.' }
$remaining = @(Get-DeploymentRemainders | Where-Object resourceGroup -ne $c.ResourceGroup)
if ($remaining.Count) {
    $remaining | Select-Object name,type,resourceGroup,id | Format-Table -AutoSize
    throw 'Potentially billable application-tagged resources remain outside the group; review manually. Nothing outside the group was deleted.'
}
Write-Host 'No application-tagged resources found outside the group. Untagged/independent backups, monitoring, other subscriptions and delayed billing are outside this verification; zero future charges is not guaranteed.'
