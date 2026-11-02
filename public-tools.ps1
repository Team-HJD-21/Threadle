$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security

function Read-ProtectedValues([string]$path) {
    $values = @{}
    $saved = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    foreach ($entry in $saved.PSObject.Properties) { $values[$entry.Name] = [string]$entry.Value }
    return $values
}

function Get-ProtectedValue($values, [string]$name) {
    if (-not $values.ContainsKey($name)) { return $null }
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($values[$name]),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    try { return [Text.Encoding]::UTF8.GetString($bytes) }
    finally { [Array]::Clear($bytes,0,$bytes.Length) }
}

function Set-ProtectedValue($values, [string]$name, [string]$value) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($value)
    try {
        $protected = [Security.Cryptography.ProtectedData]::Protect($bytes,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
        $values[$name] = [Convert]::ToBase64String($protected)
    } finally { [Array]::Clear($bytes,0,$bytes.Length) }
}

function Save-ProtectedValues($values, [string]$path) {
    $temporary = $path + '.new-' + [Guid]::NewGuid().ToString('N')
    $values | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    [IO.File]::Replace($temporary,$path,[NullString]::Value)
}

function Read-ClipboardValue([string]$label) {
    Write-Host ($label + ': copy the value, return here and press ENTER. Do not paste it into the terminal.')
    Read-Host | Out-Null
    $raw = Get-Clipboard -Raw
    if ($null -eq $raw) { throw ($label + ': clipboard is empty. Check Remote Desktop clipboard sharing and copy again.') }
    $value = $raw.Trim()
    if ($value.Length -lt 16 -or $value.Length -gt 4096 -or $value -match '\s') { throw ($label + ': invalid clipboard value; nothing was saved.') }
    Write-Host ($label + ': value received (hidden).')
    return $value
}

function Invoke-PublicCheck([string]$root, [switch]$Prepare) {
    . (Join-Path $root 'local-credentials.ps1')
    try {
        Initialize-LocalCredentials
        $runtime = Get-Content -LiteralPath (Join-Path $root 'runtime.json') -Raw | ConvertFrom-Json
        $arguments = @('-B', (Join-Path $root 'check-public.py'), '--state-dir', $root)
        if ($Prepare) { $arguments += '--prepare' }
        & $runtime.python @arguments
        if ($LASTEXITCODE -ne 0) { throw 'Public configuration verification failed. Existing service was not restarted.' }
    } finally { Clear-LocalCredentials }
}

function Restart-ReceiverChecked([switch]$Public) {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $task = $folder.GetTask('TeamHJD-GitHubDiscord-Receiver')
    if ($task.State -in @(2,4)) { $task.Stop(0) }
    $deadline = (Get-Date).AddSeconds(20)
    while ($true) {
        $task = $folder.GetTask('TeamHJD-GitHubDiscord-Receiver')
        $listening = ([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()).Port -contains 8787
        if ($task.State -notin @(2,4) -and -not $listening) { break }
        if ((Get-Date) -gt $deadline) { throw 'Receiver task or port did not stop. No unrelated process was killed.' }
        Start-Sleep -Milliseconds 300
    }
    $task.Run($null) | Out-Null
    $settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'public-settings.json') -Raw | ConvertFrom-Json
    $uri = if ($Public) { 'http://127.0.0.1:8787/connect' } else { 'http://127.0.0.1:8787/health' }
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        try {
            $response = Invoke-WebRequest $uri -Headers @{Host=([Uri]$settings.PUBLIC_BASE_URL).Authority} -UseBasicParsing -TimeoutSec 2
            $body = $response.Content
            if ($body -is [byte[]]) { $body = [Text.Encoding]::UTF8.GetString($body) }
            if ($response.StatusCode -eq 200 -and (($Public -and $body.Contains('Discord')) -or (-not $Public -and $body -eq 'receiver-ready'))) { return }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    throw 'Receiver did not respond after restart.'
}
