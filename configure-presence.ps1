#Requires -RunAsAdministrator
param(
    [ValidateLength(1,128)][string]$Text = 'GitHub updates | /connect',
    [ValidateSet('online','idle','dnd')][string]$Status = 'online',
    [switch]$Disable
)
$ErrorActionPreference = 'Stop'
if ($Text -match '[\x00-\x1F]' -or -not $Text.Trim()) { throw 'Use a nonempty, single-line activity text.' }
$root = Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'
$path = Join-Path $root 'presence.json'
$backup = Join-Path $root ('backups\presence-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
New-Item -ItemType Directory -Path $backup | Out-Null
$previous = Test-Path -LiteralPath $path
if ($previous) { Copy-Item -LiteralPath $path -Destination (Join-Path $backup 'presence.json') }
try {
    @{enabled=(-not $Disable);text=$Text;status=$Status} | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
    . (Join-Path $root 'public-tools.ps1')
    Restart-ReceiverChecked -Public
    Write-Host 'PRESENCE SETTINGS SAVED. Discord connection status must still be checked.'
    Write-Host ('Backup: ' + $backup)
} catch {
    $failure = $_
    if ($previous) { Copy-Item -LiteralPath (Join-Path $backup 'presence.json') -Destination $path -Force }
    elseif (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    Restart-ReceiverChecked -Public
    throw $failure
}
