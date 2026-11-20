param(
    [ValidateSet('None','Frontend','Experience','Cards-Agent','FinCrime-Agent','MCP-Server','Cards-API','FinCrime-API','Agent','MCP','Cards','FinCrime','All')][string]$DebugComponent = 'None',
    [switch]$Build, [switch]$Stop, [switch]$WaitForDebugger
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCommand) { $dockerPath = $dockerCommand.Source } else {
    $dockerPath = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
    if (-not (Test-Path -LiteralPath $dockerPath)) { $dockerPath = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe' }
}
if (-not (Test-Path -LiteralPath $dockerPath)) { throw 'Docker CLI not found. Install/start Docker Desktop.' }
$env:PATH = (Split-Path $dockerPath -Parent) + [IO.Path]::PathSeparator + $env:PATH
if (-not (Test-Path -LiteralPath '.env.stack')) { throw 'Run .\scripts\setup-stack.ps1 first.' }
function Invoke-Compose([string[]]$Arguments) {
    # Windows PowerShell treats redirected native stderr progress as an error
    # under Stop. The native exit code, not Docker's progress stream, is decisive.
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $dockerPath @Arguments
    $nativeCode = $LASTEXITCODE
    $ErrorActionPreference = $savedPreference
    if ($nativeCode -ne 0) { throw "Docker Compose exited with code $nativeCode." }
}
$composeArgs = @('compose')
if (Test-Path -LiteralPath '.env') { $composeArgs += @('--env-file', '.env') }
$composeArgs += @('--env-file', '.env.stack', '-f', 'compose.yaml')
if ($Stop) {
    Invoke-Compose -Arguments ($composeArgs + @('down'))
    exit 0
}
$components = @('Experience', 'Cards-Agent', 'FinCrime-Agent', 'MCP-Server', 'Cards-API', 'FinCrime-API')
$aliasMap = @{
    'Agent' = 'Cards-Agent'
    'MCP' = 'MCP-Server'
    'Cards' = 'Cards-API'
    'FinCrime' = 'FinCrime-API'
}
if ($aliasMap.ContainsKey($DebugComponent)) {
    $DebugComponent = $aliasMap[$DebugComponent]
}
if ($WaitForDebugger -and $DebugComponent -notin $components) { throw '-WaitForDebugger requires one Python component.' }
foreach ($component in $components) {
    $envKey = $component.Replace('-', '_').ToUpper()
    $enabled = if ($DebugComponent -eq 'All' -or $DebugComponent -eq $component) { '1' } else { '0' }
    [Environment]::SetEnvironmentVariable("DEBUG_$envKey", $enabled, 'Process')
    [Environment]::SetEnvironmentVariable("DEBUG_WAIT_$envKey", '0', 'Process')
}
if ($DebugComponent -ne 'None') { $composeArgs += @('-f', 'compose.debug.yaml') }
if ($Build) {
    Write-Host 'Stage 1/3: building local application images.'
    Invoke-Compose -Arguments ($composeArgs + @('build'))
}
if (-not $WaitForDebugger -or $Build) {
    Write-Host 'Stage 2/3: starting Qdrant, OPA and updating approved policy indexes.'
    Invoke-Compose -Arguments ($composeArgs + @('up', '-d', '--wait', '--wait-timeout', '120', 'qdrant', 'opa'))
    Invoke-Compose -Arguments ($composeArgs + @('run', '--rm', '--no-deps', '--workdir', '/data',
        '--entrypoint', 'python', 'cards-agent', '-m', 'card_chat.policy.pipeline.cli', 'ingest'))
    Write-Host 'Policy indexing completed. Stage 3/3: starting application containers.'
}
$upArgs = @('up', '-d')
if ($WaitForDebugger) {
    $envKey = $DebugComponent.Replace('-', '_').ToUpper()
    [Environment]::SetEnvironmentVariable("DEBUG_WAIT_$envKey", '1', 'Process')
    $serviceName = $DebugComponent.ToLower()
    $upArgs += @('--no-deps', $serviceName)
} else { $upArgs += @('--wait', '--wait-timeout', '360') }
Invoke-Compose -Arguments ($composeArgs + $upArgs)
$ports = @{
    'Experience' = 5681
    'Cards-Agent' = 5682
    'MCP-Server' = 5683
    'Cards-API' = 5684
    'FinCrime-API' = 5685
    'FinCrime-Agent' = 5686
}
foreach ($component in $components) {
    if ($DebugComponent -ne 'All' -and $DebugComponent -ne $component) { continue }
    $reopened = $false
    $deadline = (Get-Date).AddSeconds(60)
    do {
        # Inspect LISTEN state without opening a partial DAP session through
        # Docker Desktop's TCP proxy (which can delay the next real attach).
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $dockerPath @composeArgs exec -T $component.ToLower() python -c "from pathlib import Path; rows=Path('/proc/net/tcp').read_text().splitlines()[1:]; raise SystemExit(0 if any(r.split()[1].endswith(':162E') and r.split()[3]=='0A' for r in rows) else 1)" *> $null
        $ready = $LASTEXITCODE -eq 0
        $ErrorActionPreference = $savedPreference
        if (-not $ready -and -not $WaitForDebugger -and -not $reopened) {
            Write-Host "Reopening $component debugger after a previous detach."
            Invoke-Compose -Arguments ($composeArgs + @('restart', $component.ToLower()))
            $reopened = $true
        }
        if (-not $ready) { Start-Sleep -Milliseconds 250 }
    } until ($ready -or (Get-Date) -gt $deadline)
    if (-not $ready) { throw "$component debugger did not become ready." }
    if ($reopened) {
        Invoke-Compose -Arguments ($composeArgs + @('up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', $component.ToLower()))
    }
}
if (-not $WaitForDebugger) {
    $response = Invoke-WebRequest -UseBasicParsing 'http://localhost:8080/health'
    if ($response.StatusCode -ne 200) { throw 'Frontend did not become ready.' }
}
Write-Host 'Stack ready: http://localhost:8080. Credentials: .env.stack. Detaching leaves services running.'
