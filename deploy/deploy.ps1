[CmdletBinding()]
param([string]$Root = 'C:\ProgramData\TeamHJD\GitHubDiscord', [switch]$PreflightOnly)
$ErrorActionPreference = 'Stop'
$source = Split-Path $PSScriptRoot -Parent
$runtime = Get-Content -LiteralPath (Join-Path $Root 'runtime.json') -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $runtime.python -PathType Leaf)) { throw 'Configured Python was not found.' }
$lock = [IO.File]::Open((Join-Path $Root 'cd-deployment.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$job = Join-Path $Root ('cd-deployments\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [Guid]::NewGuid().ToString('N'))
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$taskName = 'TeamHJD-GitHubDiscord-Receiver'
. (Join-Path $PSScriptRoot 'recovery_settings.ps1')
$recoveryChanged = $false
$taskBackup = Join-Path $job 'receiver-task-before.xml'
function Invoke-Patch([string]$Action) {
    & $runtime.python -B (Join-Path $PSScriptRoot 'patch_runtime.py') $Action --root $Root --source $source --job $job
    if ($LASTEXITCODE -ne 0) { throw ('Deployment phase failed: ' + $Action) }
}
function Stop-Receiver {
    $task = $folder.GetTask($taskName)
    if ($task.State -in @(2,4)) { $task.Stop(0) }
    $deadline = [DateTime]::UtcNow.AddSeconds(40)
    do {
        $task = $folder.GetTask($taskName)
        $listening = ([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()).Port -contains 8787
        if ($task.State -notin @(2,4) -and -not $listening) { return }
        Start-Sleep -Milliseconds 400
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Receiver did not stop; no files were replaced.'
}
function Start-Receiver {
    $task = $folder.GetTask($taskName)
    if ($task.State -notin @(2,4)) { $task.Run($null) | Out-Null }
    $settings = Get-Content -LiteralPath (Join-Path $Root 'public-settings.json') -Raw | ConvertFrom-Json
    $hostName = ([Uri]$settings.PUBLIC_BASE_URL).Authority
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        try {
            $health = Invoke-WebRequest 'http://127.0.0.1:8787/health' -UseBasicParsing -TimeoutSec 2
            $page = Invoke-WebRequest 'http://127.0.0.1:8787/connect' -Headers @{Host=$hostName} -UseBasicParsing -TimeoutSec 2
            if ($health.StatusCode -eq 200 -and $health.Content.Trim() -eq 'receiver-ready' -and $page.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Receiver or connection page did not become healthy.'
}
$stopped = $false
$stopRequested = $false
$applying = $false
try {
    foreach ($task in $folder.GetTasks(0)) {
        if ($task.Name -eq 'TeamHJD-Threadle-Update' -and $task.State -in @(2,4)) { throw 'An admin update is running.' }
    }
    $folder.GetTask($taskName) | Out-Null
    Test-ReceiverRecoveryTask -Task ($folder.GetTask($taskName)) -Root $Root
    Invoke-Patch 'prepare'
    if ($PreflightOnly) { 'preflight' | Set-Content -LiteralPath (Join-Path $job 'result.txt'); Write-Host ('PREFLIGHT PASSED. Service was not restarted. Job: ' + $job); return }
    $stopRequested = $true
    Stop-Receiver
    $stopped = $true
    $applying = $true
    Invoke-Patch 'apply'
    $recoveryChanged = $true
    Enable-ReceiverRecovery -Folder $folder -TaskName $taskName -Root $Root -BackupFile $taskBackup
    Start-Receiver
    $stopped = $false
    'passed' | Set-Content -LiteralPath (Join-Path $job 'result.txt')
    Write-Host ('DEPLOYMENT PASSED. Backup: ' + $job)
} catch {
    $failure = $_
    if ($recoveryChanged -and (Test-Path -LiteralPath $taskBackup -PathType Leaf)) {
        try {
            $folder.RegisterTask($taskName,[IO.File]::ReadAllText($taskBackup),4,'SYSTEM',$null,5,$null) | Out-Null
            Write-Host 'Previous Receiver task settings restored.'
        } catch { Write-Error ('Receiver task restore failed: ' + $_.Exception.GetType().FullName) -ErrorAction Continue }
    }
    if ($applying) {
        try {
            Stop-Receiver
            Invoke-Patch 'restore'
            Start-Receiver
            $stopped = $false
            'rolled-back' | Set-Content -LiteralPath (Join-Path $job 'result.txt')
            Write-Host 'ROLLBACK PASSED. Previous service is responding.'
        } catch { Write-Error ('Recovery failed. Backup: ' + $job + '. ' + $_) -ErrorAction Continue }
    } elseif ($stopRequested) {
        try { Start-Receiver } catch { Write-Error $_ -ErrorAction Continue }
    }
    throw $failure
} finally { $lock.Dispose() }
