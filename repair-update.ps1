#Requires -RunAsAdministrator
param([string]$Root = (Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'), [string]$Package)
$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
if (-not $Package) { $Package = Join-Path (Split-Path $PSScriptRoot -Parent) 'Threadle-RecoveryUpdate.zip' }
$root = [IO.Path]::GetFullPath($Root)
$runtime = Get-Content -LiteralPath (Join-Path $root 'runtime.json') -Raw | ConvertFrom-Json
$helper = Join-Path $PSScriptRoot 'repair-update.py'
$savedPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & $runtime.python -B $helper verify --root $root --package $Package
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $savedPreference }
if ($code -ne 0) { throw 'Package verification failed. No application file was changed.' }
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
foreach ($task in $scheduler.GetFolder('\').GetTasks(0)) {
    if ($task.Name -eq 'TeamHJD-Threadle-Update' -and $task.State -in @(2,4)) {
        throw 'An update task is still active. No service was stopped. Wait for it to finish before recovery.'
    }
}
. (Join-Path $PSScriptRoot 'update-runtime.ps1')
. (Join-Path $root 'public-tools.ps1')
Write-Host 'Stopping only the Threadle Receiver. Caddy and Perforce are not changed.'
Stop-ThreadleReceiver -Root $root
$jobId = $null
try {
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $runtime.python -B $helper prepare --root $root --package $Package
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    if ($code -ne 0) { throw 'Could not prepare the recovery update.' }
    $jobId = [string](@($output)[-1])
    if ($jobId -notmatch '^[a-f0-9]{32}$') { throw 'Invalid recovery job identity.' }
} catch {
    Restart-ReceiverChecked -Public
    throw
}
Write-Host ('Applying cumulative recovery update. Job: ' + $jobId)
$savedPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'apply-admin-update.ps1') -JobId $jobId -StateRoot $root
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $savedPreference }
if ($code -ne 0) {
    Write-Host ('Details: ' + (Join-Path $root ('admin-updates\' + $jobId + '\apply.log')))
    throw 'Recovery update did not complete. Its log and pre-update backup were preserved.'
}
Write-Host 'RECOVERY UPDATE PASSED. The cumulative release and repaired updater are installed.'
Write-Host ('Backup and log: ' + (Join-Path $root ('admin-updates\' + $jobId)))
Write-Host 'Existing credentials, connection databases, queues, Caddy and Perforce were preserved.'
