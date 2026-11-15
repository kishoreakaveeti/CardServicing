param([switch]$DebugApi, [switch]$Build, [switch]$Standalone)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCommand) {
    $dockerPath = $dockerCommand.Source
} else {
    $dockerPath = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
    if (-not (Test-Path -LiteralPath $dockerPath)) {
        $dockerPath = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
    }
}
if (-not (Test-Path -LiteralPath $dockerPath)) { throw 'Docker CLI not found.' }
if (-not $Standalone -and -not (Test-Path -LiteralPath '.env')) { throw 'Run scripts/setup-infra.ps1 first to create .env.' }
$env:PATH = (Split-Path $dockerPath -Parent) + [IO.Path]::PathSeparator + $env:PATH
$composeFile = if ($Standalone) { 'compose.api.yaml' } else { 'compose.infra.yaml' }
$composeArgs = @('compose', '-f', $composeFile)
if ($DebugApi) { throw 'For HTTP stack debugging use scripts/stack.ps1 -DebugComponent Cards. Legacy API startup supports normal mode only.' }
$composeArgs += @('up', '-d', '--wait', '--wait-timeout', '180')
if ($Build) { $composeArgs += '--build' }
$composeArgs += 'api'
& $dockerPath @composeArgs
if ($LASTEXITCODE -ne 0) { throw 'API startup failed. Inspect docker compose logs api.' }
Write-Host 'Container Swagger: http://127.0.0.1:8011/docs (unless API_PORT was overridden)'
if ($DebugApi) { Write-Host 'In VS Code select Attach to Docker API, then press F5.' }
