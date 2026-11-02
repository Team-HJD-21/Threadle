#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'
$runner = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$runtime = Get-Content -LiteralPath (Join-Path $root 'runtime.json') -Raw | ConvertFrom-Json
$config = Get-Content -LiteralPath (Join-Path $root 'channels.json') -Raw | ConvertFrom-Json
if (-not $config.guild_id) { throw 'Existing channel configuration is missing.' }
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$task = $scheduler.GetFolder('\').GetTask('TeamHJD-GitHubDiscord-Receiver')
function Stop-ReceiverTask {
    $currentTask = $scheduler.GetFolder('\').GetTask('TeamHJD-GitHubDiscord-Receiver')
    if ($currentTask.State -in @(2,4)) { $currentTask.Stop(0) }
    $deadline = (Get-Date).AddSeconds(20)
    while ($true) {
        $currentTask = $scheduler.GetFolder('\').GetTask('TeamHJD-GitHubDiscord-Receiver')
        $listening = ([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()).Port -contains 8787
        if ($currentTask.State -notin @(2,4) -and -not $listening) { return }
        if ((Get-Date) -gt $deadline) { throw 'Receiver task or port did not stop; no additional process was killed.' }
        Start-Sleep -Milliseconds 300
    }
}
function Wait-ReceiverHealth {
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        try {
            $response = Invoke-WebRequest 'http://127.0.0.1:8787/health' -UseBasicParsing -TimeoutSec 2
            $body = $response.Content
            if ($body -is [byte[]]) { $body = [Text.Encoding]::UTF8.GetString($body) }
            if ($response.StatusCode -eq 200 -and $body -eq 'receiver-ready') { return $true }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    return $false
}
$backup = Join-Path $root ('backups\service-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
New-Item -ItemType Directory -Force $backup | Out-Null
$files = @('service.py','admin.py','registry.py','auth.py','delivery.py','alert_feed.py','discord_threads.py',
           'connect_auth.py','connect_store.py','connect_web.py','landing_web.py','discord_connect.py','channel_layouts.py','web_locale.py',
           'pr_routing.py','team_notifications.py', 'repository_summary.py', 'guild_lifecycle.py', 'legal_pages.py', 'data_retention.py', 'command_sync.py', 'notification_layout.py', 'notification_media.py', 'update-runtime.ps1', 'repair-update.py', 'repair-update.ps1', 'admin_updates.py', 'start-admin-update.ps1', 'apply-admin-update.ps1', 'changelog.py', 'web_assets/threadle-avatar.png', 'web_assets/admin-update.js', 'web_assets/controls.js', 'web_assets/controls.css','team_web.py',
           'run-receiver.ps1','run-admin.ps1','local-credentials.ps1','requirements-app.txt','README.md',
           'web_assets/notify-logo.png','web_assets/teamhjd-wordmark.png','web_assets/team-settings.js','web_assets/landing.css','command_jobs.py','product_web.py','web_assets/product.css','web_assets/threadle-icon.png','migrate-threadle-domain.ps1', 'operator_web.py', 'configure-admin.ps1', 'presence.py', 'configure-presence.ps1', 'verify-presence.py')
$presenceDirectory = Join-Path $PSScriptRoot 'vendor-presence-v1'
if (Test-Path -LiteralPath $presenceDirectory) {
    $files += @(Get-ChildItem -LiteralPath $presenceDirectory -File -Recurse | ForEach-Object { $_.FullName.Substring($PSScriptRoot.Length + 1) })
}
foreach ($name in $files) {
    if (Test-Path -LiteralPath (Join-Path $root $name)) {
        New-Item -ItemType Directory -Path (Split-Path (Join-Path $backup $name)) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $backup $name)
    }
}
$stopped = $false
try {
    foreach ($name in $files) {
        New-Item -ItemType Directory -Path (Split-Path (Join-Path $root $name)) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $root $name) -Force
    }
    $repositoryFile = Join-Path $PSScriptRoot 'repository.txt'
    if (-not (Test-Path -LiteralPath $repositoryFile)) { throw 'Create repository.txt with the existing owner/repository name first.' }
    $repository = (Get-Content -LiteralPath $repositoryFile -Raw).Trim()
    & $runner -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'run-admin.ps1') migrate-legacy --repo $repository --config (Join-Path $root 'channels.json')
    if ($LASTEXITCODE -ne 0) { throw 'Legacy configuration migration failed.' }
    & $runner -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'run-receiver.ps1') -Check
    if ($LASTEXITCODE -ne 0) { throw 'Connection validation failed.' }
    Write-Host ('Restart starts UTC: ' + [DateTime]::UtcNow.ToString('o'))
    $stopped = $true
    Stop-ReceiverTask
    $instance = $task.Run($null)
    Write-Host ('Receiver task instance: ' + $instance.InstanceGuid)
    $ready = Wait-ReceiverHealth
    if (-not $ready) { throw 'New service did not become ready.' }
    Write-Host 'SERVICE UPGRADE PASSED. Existing credentials, queue and threads preserved.'
    Write-Host ('Backup: ' + $backup)
} catch {
    $failure = $_
    if ($stopped) { Stop-ReceiverTask }
    foreach ($name in $files) {
        if (Test-Path -LiteralPath (Join-Path $backup $name)) {
            Copy-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $root $name) -Force
        }
    }
    if ($stopped) {
        $task.Run($null) | Out-Null
        if (Wait-ReceiverHealth) { Write-Host 'ROLLBACK PASSED. Previous receiver is responding.' }
        else { throw ('Upgrade failed and rollback HTTP health could not be verified. Backup: ' + $backup) }
    }
    throw $failure
}
