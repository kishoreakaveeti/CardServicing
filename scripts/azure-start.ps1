[CmdletBinding()]
param([Parameter(Mandatory)][string]$SubscriptionId, [string]$ResourceGroup='rg-cardservicing-demo', [switch]$RestartContainers)
. "$PSScriptRoot/azure-common.ps1"
$path = Read-AzureConfigPath $SubscriptionId $ResourceGroup
& "$PSScriptRoot/../infra/azure/start.ps1" -ConfigPath $path -RestartContainers:$RestartContainers
