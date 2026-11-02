$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
. (Join-Path $PSScriptRoot 'local-credentials.ps1')
try {
    Initialize-LocalCredentials
    $runtime = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.json') -Raw | ConvertFrom-Json
    & $runtime.python -B (Join-Path $PSScriptRoot 'admin.py') --state-dir $PSScriptRoot @args
    exit $LASTEXITCODE
} finally { Clear-LocalCredentials }
