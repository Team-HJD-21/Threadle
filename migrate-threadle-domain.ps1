#Requires -RunAsAdministrator
param([switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'
. (Join-Path $root 'public-tools.ps1')
$runtime = Get-Content -LiteralPath (Join-Path $root 'runtime.json') -Raw | ConvertFrom-Json
$caddyfile = Join-Path $root 'Caddyfile'
$source = Get-Content -LiteralPath $caddyfile -Raw
$oldHost = 'github-hook.teamhjd.com'
$newHost = 'threadle.teamhjd.com'
$hostPattern = '(?m)^(\s*)github-hook\.teamhjd\.com\s*\{\s*\r?$'
if ([regex]::Matches($source, $hostPattern).Count -eq 1) {
    $source = [regex]::Replace($source, $hostPattern, ('$1' + $newHost + ', ' + $oldHost + ' {'))
} elseif ($source -notmatch '(?m)^\s*threadle\.teamhjd\.com,\s*github-hook\.teamhjd\.com\s*\{') {
    throw 'Unexpected Caddy host configuration. Nothing was changed.'
}
$routePattern = '(?m)^(\s*)@receiver\s+path\s+([^\r\n]+)\r?$'
$matches = [regex]::Matches($source, $routePattern)
if ($matches.Count -ne 1) { throw 'Expected one receiver route. Nothing was changed.' }
$routes = @($matches[0].Groups[2].Value.Trim() -split '\s+')
foreach ($required in @('/', '/connect', '/connect/*', '/health', '/github/webhook', '/github/app-webhook', '/discord/interactions')) {
    if ($required -notin $routes) { throw ('Receiver route is missing: ' + $required + '. Nothing was changed.') }
}
foreach ($added in @('/robots.txt', '/sitemap.xml', '/favicon.ico')) {
    if ($added -notin $routes) { $routes += $added }
}
$source = [regex]::Replace($source, $routePattern, ('$1@receiver path ' + ($routes -join ' ')))
$candidate = Join-Path $root 'Caddyfile.threadle-candidate'
[IO.File]::WriteAllText($candidate, $source, [Text.UTF8Encoding]::new($false))
& $runtime.caddy validate --config $candidate --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw 'Caddy validation failed. Active settings were preserved.' }
$backup = Join-Path $root ('backups\domain-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath $caddyfile -Destination (Join-Path $backup 'Caddyfile')
$credentials = Join-Path $root 'credentials.json'
Copy-Item -LiteralPath $credentials -Destination (Join-Path $backup 'credentials.json')
$settingsFile = Join-Path $root 'public-settings.json'
if (Test-Path -LiteralPath $settingsFile) { Copy-Item -LiteralPath $settingsFile -Destination (Join-Path $backup 'public-settings.json') }
$credentialsChanged = $false
try {
    Copy-Item -LiteralPath $candidate -Destination $caddyfile -Force
    & $runtime.caddy reload --config $caddyfile --adapter caddyfile
    if ($LASTEXITCODE -ne 0) { throw 'Caddy reload failed.' }
    if ($PrepareOnly) {
        Write-Host 'DOMAIN PREPARED. Both hosts are served; the application origin has not changed.'
        Write-Host 'Verify https://threadle.teamhjd.com/health before changing GitHub and Discord settings.'
        Write-Host ('Backup: ' + $backup)
        return
    }
    $values = Read-ProtectedValues $credentials
    $base = Get-ProtectedValue $values 'PUBLIC_BASE_URL'
    if ($base -notin @('https://github-hook.teamhjd.com', 'https://threadle.teamhjd.com')) { throw 'Unexpected application origin.' }
    Set-ProtectedValue $values 'PUBLIC_BASE_URL' 'https://threadle.teamhjd.com'
    Save-ProtectedValues $values $credentials
    $credentialsChanged = $true
    if (Test-Path -LiteralPath $settingsFile) {
        $settings = Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json
        $settings.PUBLIC_BASE_URL = 'https://threadle.teamhjd.com'
        $settings | ConvertTo-Json | Set-Content -LiteralPath $settingsFile -Encoding UTF8
    }
    Invoke-PublicCheck $root
    Restart-ReceiverChecked -Public
    Write-Host 'DOMAIN SWITCH PASSED. New links use https://threadle.teamhjd.com.'
    Write-Host 'Old webhook routes remain available. OAuth settings and external HTTPS still need verification.'
    Write-Host ('Backup: ' + $backup)
} catch {
    $failure = $_
    Copy-Item -LiteralPath (Join-Path $backup 'Caddyfile') -Destination $caddyfile -Force
    & $runtime.caddy reload --config $caddyfile --adapter caddyfile
    if ($LASTEXITCODE -ne 0) { Write-Warning ('Caddy rollback needs attention. Backup: ' + $backup) }
    if ($credentialsChanged) {
        Copy-Item -LiteralPath (Join-Path $backup 'credentials.json') -Destination $credentials -Force
        if (Test-Path -LiteralPath (Join-Path $backup 'public-settings.json')) { Copy-Item -LiteralPath (Join-Path $backup 'public-settings.json') -Destination $settingsFile -Force }
        Restart-ReceiverChecked -Public
    }
    throw $failure
}
