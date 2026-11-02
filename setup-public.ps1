#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'public-tools.ps1')
$root = Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'
$credentialsPath = Join-Path $root 'credentials.json'
$runtime = Get-Content -LiteralPath (Join-Path $root 'runtime.json') -Raw | ConvertFrom-Json
& $runtime.python -c "import sys,struct; raise SystemExit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE -ne 0) { throw 'This offline package targets 64-bit Python 3.12. Existing service was not changed.' }
$settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'public-settings.json') -Raw | ConvertFrom-Json
$values = Read-ProtectedValues $credentialsPath
if ((Get-ProtectedValue $values 'ONBOARDING_ENABLED') -eq '1') { throw 'Public setup is already enabled. Use the service upgrade instead of initial setup.' }
$backup = Join-Path $root ('backups\public-setup-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item -LiteralPath $credentialsPath -Destination (Join-Path $backup 'credentials.json')
$caddyfile = Join-Path $root 'Caddyfile'
Copy-Item -LiteralPath $caddyfile -Destination (Join-Path $backup 'Caddyfile')
Write-Host 'This prepares public connections. Existing credentials and notification queues are retained.'
$pemInput = (Read-Host 'Full path of the GitHub App private-key .pem file').Trim().Trim('"')
$pem = Get-Item -LiteralPath $pemInput
if ($pem.PSIsContainer -or $pem.Extension -ne '.pem' -or $pem.Length -gt 131072 -or $pem.Length -lt 100) { throw 'Choose the downloaded GitHub App private-key PEM file.' }
$discordSecret = Read-ClipboardValue 'DISCORD_CLIENT_SECRET (OAuth2 client secret, not bot token)'
$githubSecret = Read-ClipboardValue 'GITHUB_APP_CLIENT_SECRET (not the private key)'
$keyDestination = Join-Path $root ('github-app-' + [Guid]::NewGuid().ToString('N') + '.pem')
Copy-Item -LiteralPath $pem.FullName -Destination $keyDestination
$vendor = Join-Path $root 'vendor-public'
& $runtime.python -m pip install --no-index --find-links (Join-Path $PSScriptRoot 'wheels') --target $vendor --upgrade -r (Join-Path $PSScriptRoot 'requirements-app.txt') --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Offline dependency installation failed. Existing service was not changed.' }
$random = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($random) } finally { $rng.Dispose() }
$sessionKey = [Convert]::ToBase64String($random).Replace('+','-').Replace('/','_')
[Array]::Clear($random,0,$random.Length)
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$random = New-Object byte[] 32
try { $rng.GetBytes($random); $webhookSecret = [Convert]::ToBase64String($random) } finally { $rng.Dispose(); [Array]::Clear($random,0,$random.Length) }
foreach ($entry in $settings.PSObject.Properties) { Set-ProtectedValue $values $entry.Name ([string]$entry.Value) }
foreach ($entry in @{
    GITHUB_APP_PRIVATE_KEY_FILE=$keyDestination; DISCORD_CLIENT_SECRET=$discordSecret;
    GITHUB_APP_CLIENT_SECRET=$githubSecret; GITHUB_APP_WEBHOOK_SECRET=$webhookSecret;
    CONNECT_SESSION_KEY=$sessionKey; ONBOARDING_ENABLED='0'
}.GetEnumerator()) { Set-ProtectedValue $values $entry.Key $entry.Value }
$discordSecret = $null; $githubSecret = $null; $webhookSecret = $null; $sessionKey = $null
$credentialsChanged = $false
$caddyChanged = $false
try {
    Save-ProtectedValues $values $credentialsPath
    $credentialsChanged = $true
    & (Join-Path $PSScriptRoot 'upgrade-service.ps1')
    foreach ($name in @('check-public.py','public-tools.ps1','public-settings.json','activate-public.ps1','copy-app-webhook-secret.ps1')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $root $name) -Force
    }
    Invoke-PublicCheck $root -Prepare
    $runner = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $publicKey = (& $runner -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'run-admin.ps1') discord-public-key | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or $publicKey -notmatch '^[a-fA-F0-9]{64}$') { throw 'Could not verify the Discord interaction public key.' }
    Set-ProtectedValue $values 'DISCORD_PUBLIC_KEY' $publicKey
    Save-ProtectedValues $values $credentialsPath
    Restart-ReceiverChecked
    $source = Get-Content -LiteralPath $caddyfile -Raw
    $pattern = '(?m)^(\s*)@receiver\s+path\s+/health\s+/github/webhook(?:\s+/github/app-webhook)?\s*$'
    if ([regex]::Matches($source,$pattern).Count -ne 1) { throw 'Unexpected Caddy routes. Configuration was preserved; ask the operator to review it.' }
    $updated = [regex]::Replace($source,$pattern,'$1@receiver path / /connect /connect/* /health /github/webhook /github/app-webhook /discord/interactions')
    $candidate = Join-Path $root 'Caddyfile.public-candidate'
    $updated | Set-Content -LiteralPath $candidate -Encoding ASCII
    & $runtime.caddy validate --config $candidate --adapter caddyfile
    if ($LASTEXITCODE -ne 0) { throw 'Caddy configuration validation failed.' }
    Copy-Item -LiteralPath $candidate -Destination $caddyfile -Force
    $caddyChanged = $true
    & $runtime.caddy reload --config $caddyfile --adapter caddyfile
    if ($LASTEXITCODE -ne 0) { throw 'Caddy reload failed.' }
    Write-Host 'PREPARATION PASSED. Existing notifications remain active. Public sign-up is still disabled.'
    Write-Host ('Backup: ' + $backup)
    Write-Host ('Next: configure the app webhook, then run ' + (Join-Path $root 'activate-public.ps1'))
} catch {
    $failure = $_
    if ($caddyChanged) {
        Copy-Item -LiteralPath (Join-Path $backup 'Caddyfile') -Destination $caddyfile -Force
        & $runtime.caddy reload --config $caddyfile --adapter caddyfile
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Caddy rollback reload requires operator attention.' }
    }
    if ($credentialsChanged) {
        Copy-Item -LiteralPath (Join-Path $backup 'credentials.json') -Destination $credentialsPath -Force
        Restart-ReceiverChecked
    }
    throw $failure
}
