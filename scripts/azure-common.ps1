# Thin CLI adapter over the established infra/azure implementation.
. "$PSScriptRoot/../infra/azure/common.ps1"

function Get-AzureSshSourceCidr {
    # Agreement between independent HTTPS services avoids guessing through a proxy/VPN.
    $addresses = @()
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    foreach ($endpoint in @('https://api.ipify.org','https://checkip.amazonaws.com')) {
        try {
            $value = ([string](Invoke-RestMethod -Uri $endpoint -TimeoutSec 10 -ErrorAction Stop)).Trim()
            $ip = [Net.IPAddress]::None
            if ($value -notmatch '^\d{1,3}(\.\d{1,3}){3}$' -or -not [Net.IPAddress]::TryParse($value, [ref]$ip)) { throw 'Not an IPv4 address.' }
            $b = $ip.GetAddressBytes()
            if ($b[0] -in @(0,10,127) -or $b[0] -ge 224 -or
                ($b[0] -eq 169 -and $b[1] -eq 254) -or
                ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or
                ($b[0] -eq 192 -and $b[1] -eq 168) -or
                ($b[0] -eq 100 -and $b[1] -ge 64 -and $b[1] -le 127)) { throw 'Not a public IPv4 address.' }
            $addresses += $ip.ToString()
        } catch { $addresses += $null }
    }
    if ($addresses.Count -eq 2 -and $addresses[0] -and $addresses[0] -eq $addresses[1]) {
        Write-Host "Detected SSH source: $($addresses[0])/32 (administration only; the HTTPS app remains public)."
        return $addresses[0] + '/32'
    }
    Write-Warning 'Could not reliably detect a matching public IPv4 address. Enter the approved SSH CIDR explicitly; see AZURE_DEPLOYMENT.md.'
    return Read-Host 'Approved public IPv4 CIDR (your public IP followed by /32)'
}

function Initialize-AzureAnthropicKey([string]$Path) {
    $replaceExisting = Test-Path -LiteralPath $Path -PathType Leaf
    if ($replaceExisting) {
        $answer = ([string](Read-Host 'An Anthropic key is saved. Change it? [y/N]')).Trim()
        if ($answer -notmatch '^(y|yes)$') {
            Write-Host 'Reusing the saved Anthropic key.'
            return
        }
    } elseif (Test-Path -LiteralPath $Path) { throw 'AnthropicKeyFile must name a file, not a directory.' }
    $secret = Read-Host 'Paste your Anthropic API key (hidden; saved securely for future deployments)' -AsSecureString
    $pointer = [IntPtr]::Zero
    $plainKey = $null
    $stage = $null
    $temporaryKey = $null
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim()
        if ($plainKey -notmatch '^[A-Za-z0-9_-]{20,}$') { throw 'The API key is empty or has an invalid format. Rerun create and paste the key without quotes.' }
        $parent = Split-Path -Parent ([IO.Path]::GetFullPath($Path))
        New-Item -ItemType Directory -Force $parent | Out-Null
        $stage = Join-Path $parent ('.cardservicing-key-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory $stage | Out-Null
        if ($env:OS -eq 'Windows_NT') {
            Set-PrivateWindowsAccess $stage
        } else { Invoke-Native 'chmod' @('700',$stage) | Out-Null }
        $temporaryKey = Join-Path $stage 'key.txt'
        [IO.File]::WriteAllText($temporaryKey, $plainKey, [Text.UTF8Encoding]::new($false))
        if ($env:OS -eq 'Windows_NT') {
            Set-PrivateWindowsAccess $temporaryKey
        } else { Invoke-Native 'chmod' @('600',$temporaryKey) | Out-Null }
        if ($replaceExisting) {
            # Validate/stage first, then atomically replace. Keep the old key on failure;
            # do not create a plaintext backup or loosen destination permissions.
            if ($env:OS -eq 'Windows_NT') { Set-PrivateWindowsAccess $Path }
            else { Invoke-Native 'chmod' @('600',$Path) | Out-Null }
            # Windows PowerShell converts $null string arguments to an empty path.
            [IO.File]::Replace($temporaryKey, [IO.Path]::GetFullPath($Path), [NullString]::Value)
        } else {
            [IO.File]::Move($temporaryKey, [IO.Path]::GetFullPath($Path))
        }
        Write-Host 'Protected Anthropic key saved. Continuing deployment.'
    } finally {
        if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        $plainKey = $null
        if ($secret) { $secret.Dispose() }
        if ($temporaryKey -and (Test-Path -LiteralPath $temporaryKey)) { [IO.File]::Delete($temporaryKey) }
        if ($stage -and (Test-Path -LiteralPath $stage)) { [IO.Directory]::Delete($stage) }
    }
}

function Get-AzureConfigPath([string]$SubscriptionId, [string]$ResourceGroup) {
    $id = [guid]::Empty
    if (-not [guid]::TryParse($SubscriptionId, [ref]$id)) { throw 'SubscriptionId must be a UUID.' }
    if ($ResourceGroup -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$') { throw 'Invalid resource group name.' }
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $suffix = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($id.ToString() + '/' + $ResourceGroup)))).Replace('-','').Substring(0,12).ToLowerInvariant() }
    finally { $hash.Dispose() }
    $script:AdapterId = 'cardservicing-' + $suffix
    return Join-Path $script:RepoRoot ('.azure/' + $script:AdapterId + '/deployment.json')
}

function Read-AzureConfigPath([string]$SubscriptionId, [string]$ResourceGroup) {
    $path = Get-AzureConfigPath $SubscriptionId $ResourceGroup
    if (-not (Test-Path -LiteralPath $path)) { throw 'Deployment configuration missing. Use the original checkout/state or infra/azure commands with the saved -ConfigPath; do not guess deployment ownership.' }
    $saved = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
    if ($saved.SubscriptionId -ne $SubscriptionId -or $saved.ResourceGroup -ne $ResourceGroup) { throw 'Saved deployment scope does not match.' }
    return $path
}
