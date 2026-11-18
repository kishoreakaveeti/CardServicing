[CmdletBinding()]
param([Parameter(Mandatory)][string]$SubscriptionId, [string]$ResourceGroup='rg-cardservicing-demo')
. "$PSScriptRoot/azure-common.ps1"
$path = Read-AzureConfigPath $SubscriptionId $ResourceGroup
& "$PSScriptRoot/../infra/azure/stop.ps1" -ConfigPath $path
