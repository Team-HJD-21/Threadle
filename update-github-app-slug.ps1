#Requires -RunAsAdministrator
param([ValidatePattern('^[a-z0-9][a-z0-9-]{0,99}$')][string]$Slug = 'teamhjd-threadle')
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'
. (Join-Path $root 'public-tools.ps1')
$credentials = Join-Path $root 'credentials.json'
$settingsPath = Join-Path $root 'public-settings.json'
$values = Read-ProtectedValues $credentials
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$backup = Join-Path $root ('backups\app-name-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath $credentials -Destination (Join-Path $backup 'credentials.json')
Copy-Item -LiteralPath $settingsPath -Destination (Join-Path $backup 'public-settings.json')
try {
    Set-ProtectedValue $values 'GITHUB_APP_SLUG' $Slug
    Save-ProtectedValues $values $credentials
    $settings.GITHUB_APP_SLUG = $Slug
    $settings | ConvertTo-Json | Set-Content -LiteralPath $settingsPath -Encoding UTF8
    Invoke-PublicCheck $root
    Restart-ReceiverChecked -Public
    Write-Host ('APP NAME UPDATED: ' + $Slug)
    Write-Host ('Install URL: https://github.com/apps/' + $Slug + '/installations/new')
    Write-Host 'Existing tokens, repository connections and notification queues were preserved.'
    Write-Host ('Backup: ' + $backup)
} catch {
    $failure = $_
    Copy-Item -LiteralPath (Join-Path $backup 'credentials.json') -Destination $credentials -Force
    Copy-Item -LiteralPath (Join-Path $backup 'public-settings.json') -Destination $settingsPath -Force
    Restart-ReceiverChecked -Public
    throw $failure
}
