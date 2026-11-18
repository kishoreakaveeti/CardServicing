[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath,
      [Parameter(Mandatory)][ValidatePattern('^age1[0-9a-z]{58}$')][string]$AgeRecipient,
      [string]$OutputDirectory = '.azure/backups')
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
$address = Get-VmAddress
Invoke-Remote $address ("sudo bash /opt/cardservicing/app/infra/azure/control.sh backup " + $AgeRecipient)
$remoteName = (Invoke-Remote $address 'sudo cat /opt/cardservicing/state/last-backup-name').Trim()
if ($remoteName -notmatch '^backup-[0-9TZ-]+-[a-f0-9]{12}\.tar\.gz\.age$') { throw 'Invalid backup name returned by VM.' }
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
Invoke-Native 'scp' ((Get-SshOptions) + @(("{0}@{1}:/opt/cardservicing/backups/{2}" -f $c.AdminUsername,$address,$remoteName),$OutputDirectory)) | Out-Null
Write-Host "Encrypted backup downloaded to $OutputDirectory. Keep the age private identity separately. Backup storage continues incurring disk costs."
