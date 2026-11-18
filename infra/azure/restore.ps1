[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath,
      [Parameter(Mandatory)][string]$BackupFile,
      [Parameter(Mandatory)][string]$AgeIdentityFile,
      [Parameter(Mandatory)][string]$ConfirmRestoreDeployment)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
if ($ConfirmRestoreDeployment -cne $c.DeploymentId) { throw 'Restore replaces persistent state. Supply the exact DeploymentId to confirm.' }
foreach ($path in @($BackupFile,$AgeIdentityFile)) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Backup or age identity file is missing.' } }
$address = Get-VmAddress
Invoke-Remote $address 'umask 077; mkdir -p .cardservicing-upload; chmod 700 .cardservicing-upload' | Out-Null
try {
    Send-File $address (Resolve-Path -LiteralPath $BackupFile).Path '.cardservicing-upload/restore.tar.gz.age'
    Send-File $address (Resolve-Path -LiteralPath $AgeIdentityFile).Path '.cardservicing-upload/restore.agekey'
    $remoteBase = '/home/' + $c.AdminUsername + '/.cardservicing-upload/'
    Invoke-Remote $address ('sudo bash /opt/cardservicing/app/infra/azure/control.sh restore ' + $remoteBase + 'restore.tar.gz.age ' + $remoteBase + 'restore.agekey')
} finally {
    Invoke-Remote $address 'rm -f -- .cardservicing-upload/restore.agekey .cardservicing-upload/restore.tar.gz.age' | Out-Null
}
& "$PSScriptRoot/start.ps1" -ConfigPath $ConfigPath
& "$PSScriptRoot/smoke-test.ps1" -ConfigPath $ConfigPath
