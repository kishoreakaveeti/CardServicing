[CmdletBinding()]
param()
. "$PSScriptRoot/common.ps1"
# This entry point does not call az or provision anything.
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    foreach ($candidate in @("$env:LOCALAPPDATA/Programs/DockerDesktop/resources/bin/docker.exe", "$env:ProgramFiles/Docker/Docker/resources/bin/docker.exe")) {
        if (Test-Path -LiteralPath $candidate) { $env:PATH = (Split-Path $candidate -Parent) + [IO.Path]::PathSeparator + $env:PATH; break }
    }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is required for Linux preflight.' }
# Host Python is needed only for Docker subprocess orchestration (stdlib only).
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Install Python 3.10+ for the standard-library-only Docker preflight runner.' }
Write-Host 'Local preflight is running on this computer, not Azure. Progress and Docker build output follow.'
Invoke-Native $python.Source @('-u',(Join-Path $PSScriptRoot 'validate_local.py')) -Stream
