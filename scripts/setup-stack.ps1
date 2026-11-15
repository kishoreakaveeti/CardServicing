$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& .\.venv\Scripts\python.exe scripts/setup_stack.py
if ($LASTEXITCODE -ne 0) { throw 'Setup stopped; inspect the message above.' }
