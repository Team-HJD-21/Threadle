#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'public-tools.ps1')
$root = Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'
$credentialsPath = Join-Path $root 'credentials.json'
Invoke-PublicCheck $root
$runner = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
& $runner -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'run-admin.ps1') register-discord-command
if ($LASTEXITCODE -ne 0) { throw 'Discord command registration failed. Public connections remain disabled.' }
$values = Read-ProtectedValues $credentialsPath
$previous = Get-ProtectedValue $values 'ONBOARDING_ENABLED'
if ($previous -eq '1') { Write-Host 'Public connections are already enabled.'; exit 0 }
try {
    Set-ProtectedValue $values 'ONBOARDING_ENABLED' '1'
    Save-ProtectedValues $values $credentialsPath
    Restart-ReceiverChecked -Public
    Write-Host 'PUBLIC CONNECTION PAGE ENABLED. External HTTPS and real Discord/GitHub login verification are still required.'
    Write-Host 'Open https://threadle.teamhjd.com/connect in a browser.'
} catch {
    Set-ProtectedValue $values 'ONBOARDING_ENABLED' $previous
    Save-ProtectedValues $values $credentialsPath
    Restart-ReceiverChecked
    throw
}
