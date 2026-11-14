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
if (-not (Test-Path -LiteralPath $dockerPath)) {
    throw 'Docker CLI not found. Restart your terminal after installing Docker Desktop.'
}
# Docker also finds its credential helper through PATH.
$env:PATH = (Split-Path $dockerPath -Parent) + [IO.Path]::PathSeparator + $env:PATH
if (-not (Test-Path -LiteralPath '.env')) {
    $configText = Get-Content -Raw '.env.example'
    $configText = $configText.Replace('MONGO_PASSWORD=GENERATE_ME', "MONGO_PASSWORD=$([guid]::NewGuid().ToString('N'))")
    $configText = $configText.Replace('RABBITMQ_PASSWORD=GENERATE_ME', "RABBITMQ_PASSWORD=$([guid]::NewGuid().ToString('N'))")
    $configText = $configText.Replace('ACCOUNT_HASH_KEY=GENERATE_ME', "ACCOUNT_HASH_KEY=$([guid]::NewGuid().ToString('N'))$([guid]::NewGuid().ToString('N'))")
    Set-Content -LiteralPath '.env' -Value $configText -Encoding ascii
}
if (-not (Select-String -LiteralPath '.env' -Pattern '^ACCOUNT_HASH_KEY=' -Quiet)) {
    Add-Content -LiteralPath '.env' -Value "`nACCOUNT_HASH_KEY=$([guid]::NewGuid().ToString('N'))$([guid]::NewGuid().ToString('N'))`nACCOUNT_HASH_KEY_VERSION=v1" -Encoding ascii
}
& $dockerPath compose up -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) { throw 'Infrastructure startup failed. Check Docker Desktop and compose logs.' }
& $dockerPath compose ps
