[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory)][string]$SubscriptionId, [string]$ResourceGroup='rg-cardservicing-demo')
. "$PSScriptRoot/azure-common.ps1"
$path = Read-AzureConfigPath $SubscriptionId $ResourceGroup
& "$PSScriptRoot/../infra/azure/destroy.ps1" -ConfigPath $path -WhatIf:$WhatIfPreference
