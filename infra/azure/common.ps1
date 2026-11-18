# Shared helpers: dot-sourcing does not make Azure calls.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path

function Invoke-Native([string]$Program, [string[]]$Arguments, [switch]$Stream) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $result = $null
        if ($Stream) { & $Program @Arguments | ForEach-Object { Write-Host $_ } }
        else { $result = & $Program @Arguments }
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $saved }
    if ($code -ne 0) { throw "$Program failed with exit code $code." }
    return $result
}
function Read-Deployment([string]$ConfigPath) {
    $resolved = (Resolve-Path -LiteralPath $ConfigPath).Path
    $c = Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json
    foreach ($field in @('SubscriptionId','DeploymentId','ResourceGroup','Location','VmName','VmSize',
        'AdminUsername','SshPrivateKey','SshSourceCidr','StorageAccount','StorageContainer','AppDomain',
        'IdentityDomain','AcmeEmail','AnthropicKeyFile','ModelId','OsDiskSizeGB')) {
        if (-not $c.PSObject.Properties[$field] -or [string]::IsNullOrWhiteSpace([string]$c.$field)) { throw "Missing configuration: $field" }
        if ([string]$c.$field -match 'REPLACE|change-me|[\r\n]') { throw "Replace placeholder: $field" }
    }
    $id = [guid]::Empty
    if (-not [guid]::TryParse($c.SubscriptionId, [ref]$id)) { throw 'SubscriptionId must be a UUID.' }
    if ($c.Location -ne 'australiaeast') { throw 'This demo deployment requires australiaeast.' }
    foreach ($field in @('DeploymentId','ResourceGroup','VmName','VmSize')) {
        if ($c.$field -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$') { throw "Unsafe identifier: $field" }
    }
    if ($c.AdminUsername -notmatch '^[a-z][a-z0-9_-]{2,30}$') { throw 'Invalid AdminUsername.' }
    foreach ($field in @('AppDomain','IdentityDomain')) {
        if ($c.$field -notmatch '^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$' -or $c.$field -match '\.invalid$|\.example$|example\.com$|localhost') { throw "Use an owned public DNS hostname: $field" }
    }
    if ($c.AppDomain -eq $c.IdentityDomain) { throw 'Application and identity hostnames must differ.' }
    if ($c.AcmeEmail -notmatch '^[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$') { throw 'Invalid AcmeEmail.' }
    if ($c.StorageAccount -notmatch '^[a-z0-9]{3,24}$' -or $c.StorageContainer -notmatch '^[a-z0-9](?:[a-z0-9]|-(?!-)){1,61}[a-z0-9]$') { throw 'Invalid storage account/container name.' }
    if ($c.SshSourceCidr -notmatch '^(\d{1,3}\.){3}\d{1,3}/(2[4-9]|3[0-2])$') { throw 'SSH source must be a restricted IPv4 CIDR (/24 to /32), preferably your public IP/32.' }
    $ip = [System.Net.IPAddress]::None
    if (-not [System.Net.IPAddress]::TryParse(($c.SshSourceCidr -split '/')[0], [ref]$ip)) { throw 'Invalid SSH source address.' }
    if ([int]$c.OsDiskSizeGB -lt 128) { throw 'Allow at least 128 GiB persistent OS storage for images and data.' }
    if ($c.ModelId -notmatch '^[A-Za-z0-9_.:-]+$') { throw 'Invalid ModelId.' }
    foreach ($field in @('SshPrivateKey','AnthropicKeyFile')) {
        $c.$field = [Environment]::ExpandEnvironmentVariables($c.$field)
        if ($c.$field.StartsWith('~/')) { $c.$field = Join-Path $HOME $c.$field.Substring(2) }
        if (-not [IO.Path]::IsPathRooted($c.$field)) { $c.$field = Join-Path (Split-Path $resolved -Parent) $c.$field }
    }
    $script:Cfg = $c
    $script:StateDir = Join-Path $script:RepoRoot ('.azure/' + $c.DeploymentId)
    New-Item -ItemType Directory -Force $script:StateDir | Out-Null
    if ($env:OS -eq 'Windows_NT') {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            [Security.Principal.WindowsIdentity]::GetCurrent().Name, 'FullControl',
            'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $script:StateDir -AclObject $acl
    } else {
        Invoke-Native 'chmod' @('700', $script:StateDir) | Out-Null
    }
    $script:KnownHosts = Join-Path $script:StateDir 'known_hosts'
    return $c
}
function Assert-Tools([switch]$Docker) {
    foreach ($name in @('az','ssh','scp','tar')) {
        if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Install $name and put it on PATH." }
    }
    if ($Docker -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
        $candidates = @("$env:LOCALAPPDATA/Programs/DockerDesktop/resources/bin/docker.exe", "$env:ProgramFiles/Docker/Docker/resources/bin/docker.exe")
        $candidate = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $candidate) { throw 'Docker CLI is required for preflight validation.' }
        $env:PATH = (Split-Path $candidate -Parent) + [IO.Path]::PathSeparator + $env:PATH
    }
}
function Invoke-Az([string[]]$Arguments) {
    $result = Invoke-Native 'az' ($Arguments + @('--subscription', $script:Cfg.SubscriptionId, '--only-show-errors','--output','json'))
    if ($result) { return ($result -join "`n" | ConvertFrom-Json) }
}
function Assert-Login {
    $account = Invoke-Az -Arguments @('account','show')
    if ($account.id -ne $script:Cfg.SubscriptionId -or $account.state -ne 'Enabled') { throw 'Run az login and verify the configured subscription.' }
}
function Get-OwnedGroup {
    $exists = Invoke-Az -Arguments @('group','exists','--name',$script:Cfg.ResourceGroup)
    if (-not $exists) { return $null }
    $group = Invoke-Az -Arguments @('group','show','--name',$script:Cfg.ResourceGroup)
    if (-not $group.tags -or -not $group.tags.PSObject.Properties['cardservicingDeployment'] -or $group.tags.cardservicingDeployment -ne $script:Cfg.DeploymentId) {
        throw 'Resource group ownership tag does not match. Refusing to manage unrelated resources.'
    }
    return $group
}
function Assert-ExpectedResources {
    $c = $script:Cfg
    $expected = @{
        'Microsoft.Network/virtualNetworks' = $c.VmName + '-vnet'
        'Microsoft.Network/networkSecurityGroups' = $c.VmName + '-nsg'
        'Microsoft.Network/publicIPAddresses' = $c.VmName + '-ip'
        'Microsoft.Network/networkInterfaces' = $c.VmName + '-nic'
        'Microsoft.Compute/virtualMachines' = $c.VmName
        'Microsoft.Compute/disks' = $c.VmName + '-os'
        'Microsoft.Storage/storageAccounts' = $c.StorageAccount
    }
    foreach ($resource in @(Invoke-Az -Arguments @('resource','list','-g',$c.ResourceGroup))) {
        $approved = $false
        foreach ($kind in $expected.Keys) {
            if (($resource.type -eq $kind -and $resource.name -eq $expected[$kind]) -or
                ($resource.type.StartsWith($kind + '/', [StringComparison]::OrdinalIgnoreCase) -and
                 $resource.name.StartsWith($expected[$kind] + '/', [StringComparison]::OrdinalIgnoreCase))) {
                $approved = $true
            }
        }
        if (-not $approved) {
            throw 'Unexpected resource in the dedicated group. Refusing to alter or destroy unrelated resources.'
        }
    }
}
function Get-VmAddress {
    if (-not (Get-OwnedGroup)) { throw 'Dedicated resource group does not exist.' }
    $publicIp = Invoke-Az -Arguments @('network','public-ip','show','-g',$script:Cfg.ResourceGroup,'-n',($script:Cfg.VmName + '-ip'))
    if (-not $publicIp.ipAddress) { throw 'VM has no allocated public address.' }
    return $publicIp.ipAddress
}
function Set-TrustedHost([string]$Address) {
    # Host key comes from the authenticated Azure control plane, not ssh-keyscan.
    $result = Invoke-Az -Arguments @('vm','run-command','invoke','-g',$script:Cfg.ResourceGroup,'-n',$script:Cfg.VmName,
        '--command-id','RunShellScript','--scripts','cat /etc/ssh/ssh_host_ed25519_key.pub')
    $match = [regex]::Match(($result.value.message -join "`n"), '(?m)^ssh-ed25519 ([A-Za-z0-9+/=]+)')
    if (-not $match.Success) { throw 'Could not retrieve the VM SSH host key from Azure.' }
    [IO.File]::WriteAllText($script:KnownHosts, "$Address ssh-ed25519 $($match.Groups[1].Value)`n", [Text.UTF8Encoding]::new($false))
}
function Get-SshOptions {
    if (-not (Test-Path -LiteralPath $script:Cfg.SshPrivateKey)) { throw 'SSH private key is missing.' }
    if (-not (Test-Path -LiteralPath $script:KnownHosts)) { throw 'Trusted host key missing. Run deploy.ps1 or start.ps1 first.' }
    return @('-i',$script:Cfg.SshPrivateKey,'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
        '-o',('UserKnownHostsFile=' + $script:KnownHosts),'-o','ConnectTimeout=10','-o','ServerAliveInterval=30')
}
function Invoke-Remote([string]$Address, [string]$Command, [switch]$Stream) {
    Invoke-Native 'ssh' ((Get-SshOptions) + @(("{0}@{1}" -f $script:Cfg.AdminUsername,$Address),$Command)) -Stream:$Stream
}
function Send-File([string]$Address, [string]$Source, [string]$Destination) {
    Invoke-Native 'scp' ((Get-SshOptions) + @($Source,("{0}@{1}:{2}" -f $script:Cfg.AdminUsername,$Address,$Destination))) | Out-Null
}
function Wait-Ssh([string]$Address) {
    $deadline = (Get-Date).AddMinutes(5)
    do {
        try { Invoke-Remote $Address 'true' | Out-Null; return } catch { Start-Sleep -Seconds 5 }
    } while ((Get-Date) -lt $deadline)
    throw 'SSH readiness timed out. Verify the configured source CIDR and your SSH key.'
}
function Write-CostReminder {
    Write-Host 'Deallocation stops VM compute billing; managed disks, static public IP and Blob storage can still incur costs.'
}
