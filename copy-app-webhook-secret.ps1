#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'public-tools.ps1')
$root = Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'
$values = Read-ProtectedValues (Join-Path $root 'credentials.json')
$secret = Get-ProtectedValue $values 'GITHUB_APP_WEBHOOK_SECRET'
if (-not $secret) { throw 'Complete setup-public.ps1 first.' }
Set-Clipboard -Value $secret
$secret = $null
Write-Host 'App webhook secret copied. Paste it into the GitHub App Webhook secret field; do not paste it into chat.'
