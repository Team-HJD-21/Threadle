param([switch]$Check, [switch]$Smoke, [switch]$SmokeAlerts)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
. (Join-Path $PSScriptRoot 'local-credentials.ps1')
try {
    Initialize-LocalCredentials
    $runtime = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.json') -Raw | ConvertFrom-Json
    $arguments = @('-B', (Join-Path $PSScriptRoot 'service.py'), '--state-dir', $PSScriptRoot)
    if ($Check) { $arguments += '--check' }
    if ($Smoke) { $arguments += '--smoke' }
    if ($SmokeAlerts) { $arguments += '--smoke-alerts' }
    & $runtime.python @arguments
    exit $LASTEXITCODE
} finally { Clear-LocalCredentials }
