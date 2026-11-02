#Requires -RunAsAdministrator
param(
    [string]$TeamDomain,
    [string]$Audience,
    [string[]]$Emails,
    [switch]$Disable
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'
$path = Join-Path $root 'admin-access.json'
if (-not $Disable) {
    if ($TeamDomain -notmatch '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com$') { throw 'Provide the Cloudflare Access team domain, without https:// or a path.' }
    if ($Audience -notmatch '^[a-fA-F0-9]{64}$') { throw 'Provide the 64-character Application Audience (AUD) tag.' }
    if (-not $Emails -or @($Emails | Where-Object { $_ -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$' }).Count) { throw 'Provide the allowed administrator email addresses.' }
}
$backup = Join-Path $root ('backups\admin-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
New-Item -ItemType Directory -Path $backup | Out-Null
$hadPrevious = Test-Path -LiteralPath $path
if ($hadPrevious) { Copy-Item -LiteralPath $path -Destination (Join-Path $backup 'admin-access.json') }
try {
    $config = @{enabled=(-not $Disable);team_domain=$TeamDomain;audience=$Audience;emails=@($Emails)}
    [IO.File]::WriteAllText($path, ($config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    . (Join-Path $root 'public-tools.ps1')
    Restart-ReceiverChecked -Public
    if ($Disable) { Write-Host 'ADMIN DISABLED. Administrative requests are now denied.' }
    else {
        Write-Host 'ADMIN CONFIGURATION SAVED. Cloudflare Access JWT verification is required on every admin request.'
        Write-Host 'Open your current service origin followed by /connect/admin after completing Access setup.'
    }
    Write-Host ('Backup: ' + $backup)
} catch {
    $failure = $_
    if ($hadPrevious) { Copy-Item -LiteralPath (Join-Path $backup 'admin-access.json') -Destination $path -Force }
    elseif (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    Restart-ReceiverChecked -Public
    throw $failure
}
