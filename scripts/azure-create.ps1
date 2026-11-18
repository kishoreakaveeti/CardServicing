#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SubscriptionId,
    [string]$ResourceGroup = 'rg-cardservicing-demo',
    [string]$Location = 'australiaeast',
    [string]$VmName = 'vm-cardservicing-demo',
    [string]$VmSize = 'Standard_B4ms',
    [string]$DnsLabel,
    [string]$SshSourceCidr,
    [string]$AnthropicKeyFile,
    [string]$ModelId = 'claude-haiku-5-5',
    [string]$AcmeEmail
)
. "$PSScriptRoot/azure-common.ps1"
Write-DeploymentStage 'Stage 1/9: checking workstation tools and loading deployment configuration. No Azure resources are created in this stage.'
foreach ($tool in @('az','ssh','scp','ssh-keygen','git','tar','python')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Install $tool and reopen VS Code." }
}
$path = Get-AzureConfigPath $SubscriptionId $ResourceGroup
$previousConfig = $null
if (Test-Path -LiteralPath $path) {
    $path = Read-AzureConfigPath $SubscriptionId $ResourceGroup
    $previousConfig = Get-Content -Raw -LiteralPath $path
    $c = $previousConfig | ConvertFrom-Json
    foreach ($field in @('VmName','Location')) {
        if ($PSBoundParameters.ContainsKey($field) -and $PSBoundParameters[$field] -ne $c.$field) {
            throw "$field cannot change for an existing deployment. Use its original configuration."
        }
    }
    foreach ($field in @('Location','VmName','VmSize','DnsLabel','SshSourceCidr','AnthropicKeyFile','ModelId','AcmeEmail')) {
        if ($PSBoundParameters.ContainsKey($field)) { $c.$field = $PSBoundParameters[$field] }
    }
} else {
    if (-not $DnsLabel) { $DnsLabel = $script:AdapterId }
    if (-not $SshSourceCidr) { $SshSourceCidr = Get-AzureSshSourceCidr }
    if (-not $AnthropicKeyFile) { $AnthropicKeyFile = Join-Path $HOME '.secrets/cardservicing-anthropic-key.txt' }
    $hostName = "$DnsLabel.$Location.cloudapp.azure.com"
    if (-not $AcmeEmail) { $AcmeEmail = "operator@$hostName" }
    $c = [pscustomobject]@{
        SubscriptionId=$SubscriptionId; DeploymentId=$script:AdapterId; ResourceGroup=$ResourceGroup
        Location=$Location; VmName=$VmName; VmSize=$VmSize; AdminUsername='azureuser'
        SshPrivateKey=(Join-Path (Split-Path $path) 'ssh-key'); SshSourceCidr=$SshSourceCidr
        StorageAccount=('cs' + $script:AdapterId.Substring(14)); StorageContainer='telemetry'
        DnsLabel=$DnsLabel; AppDomain=$hostName; IdentityDomain=$hostName; AcmeEmail=$AcmeEmail
        AnthropicKeyFile=$AnthropicKeyFile; ModelId=$ModelId; OsDiskSizeGB=128
    }
}
if (-not $PSBoundParameters.ContainsKey('ModelId') -and $c.ModelId -in @('claude-sonnet-4-5','claude-sonnet-4-6')) {
    $c.ModelId = $ModelId
    Write-Host "Updating the previous Azure model default to $ModelId."
}
$c.PSObject.Properties.Remove('CostControl') # Discard obsolete local settings; no Azure budget API is used.
if ($c.DnsLabel -notmatch '^[a-z][a-z0-9-]{1,61}[a-z0-9]$') { throw 'Invalid Azure DNS label.' }
$c.AppDomain = "$($c.DnsLabel).$($c.Location).cloudapp.azure.com"
$c.IdentityDomain = $c.AppDomain
New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
[IO.File]::WriteAllText($path, ($c | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
try { $c = Read-Deployment $path } # Validate and restrict ACL before generating a key.
catch {
    if ($null -ne $previousConfig) { [IO.File]::WriteAllText($path, $previousConfig, [Text.UTF8Encoding]::new($false)) }
    else { Remove-Item -LiteralPath $path -Force }
    throw
}
Write-Host "Deployment configuration: $path"
Write-Host "Target subscription: $($c.SubscriptionId); region: $($c.Location); resource group: $($c.ResourceGroup)"
Write-DeploymentStage 'Stage 2/9: preparing protected model credentials and SSH key.'
Initialize-AzureAnthropicKey $c.AnthropicKeyFile
if (-not (Test-Path -LiteralPath $c.SshPrivateKey)) {
    $emptyPassphrase = if ($PSVersionTable.PSVersion.Major -le 5) { '""' } else { '' }
    Invoke-Native 'ssh-keygen' @('-q','-t','ed25519','-f',$c.SshPrivateKey,'-N',$emptyPassphrase) | Out-Null
}
& "$PSScriptRoot/../infra/azure/deploy.ps1" -ConfigPath $path
