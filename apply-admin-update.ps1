param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$JobId,
    [string]$StateRoot = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'

$root = [IO.Path]::GetFullPath($StateRoot)
$job = Join-Path $root ('admin-updates\' + $JobId)
$driver = Join-Path $job 'driver.py'
$runtimePath = Join-Path $root 'runtime.json'
$log = Join-Path $job 'apply.log'
$runtime = $null
$stopped = $false
$phase = 'startup'

if (-not (Test-Path -LiteralPath $job -PathType Container)) {
    New-Item -ItemType Directory -Path $job -Force | Out-Null
}

function Write-UpdateLog([string]$message) {
    $message = $message -replace '(?i)(Bearer|Bot)\s+\S+', '$1 [redacted]'
    $message = $message -replace '(?i)(github_pat_|ghp_)[A-Za-z0-9_]+', '[redacted]'
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ([DateTime]::UtcNow.ToString('o') + ' ' + $message)
}

function Invoke-UpdateDriver([string]$Action, [string]$Detail = '', [int[]]$Allowed = @(0)) {
    if ($null -eq $runtime -or [string]::IsNullOrWhiteSpace([string]$runtime.python)) {
        throw 'runtime.json did not provide a Python executable.'
    }
    if (-not (Test-Path -LiteralPath $runtime.python -PathType Leaf)) {
        throw ('Configured Python executable is missing or unreadable: ' + $runtime.python)
    }
    if (-not (Test-Path -LiteralPath $driver -PathType Leaf)) {
        throw ('Update driver is missing or unreadable: ' + $driver)
    }
    $arguments = @('-B', $driver, '--root', $root, '--job', $JobId)
    if ($Detail) { $arguments += @('--detail', $Detail) }
    $arguments += $Action
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $runtime.python @arguments *>> $log
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    if ($code -notin $Allowed) { throw ("Driver action '" + $Action + "' failed: exit=" + $code) }
    return $code
}

function Try-MarkFailed([string]$Detail) {
    try {
        Invoke-UpdateDriver -Action 'failed' -Detail $Detail | Out-Null
        Write-UpdateLog 'Update state recorded as failed.'
    } catch {
        Write-UpdateLog ('Could not record update state: ' + $_.Exception.Message)
    }
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Write-UpdateLog ('START identity=' + $identity + ' job=' + $JobId + ' root=' + $root)
    Write-UpdateLog ('Bootstrap paths runtime=' + $runtimePath + ' driver=' + $driver)

    $phase = 'reading-runtime'
    if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
        throw ('Runtime configuration is missing or unreadable: ' + $runtimePath)
    }
    $runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
    if ($null -eq $runtime -or [string]::IsNullOrWhiteSpace([string]$runtime.python)) {
        throw 'Runtime configuration does not contain a Python executable.'
    }
    if (-not (Test-Path -LiteralPath $runtime.python -PathType Leaf)) {
        throw ('Configured Python executable is missing or unreadable: ' + $runtime.python)
    }
    if (-not (Test-Path -LiteralPath $driver -PathType Leaf)) {
        throw ('Update driver is missing or unreadable: ' + $driver)
    }
    Write-UpdateLog ('Runtime and update driver are readable. Python=' + $runtime.python)

    $phase = 'checking-state'
    $check = Invoke-UpdateDriver -Action 'needs-run' -Allowed @(0,3)
    if ($check -eq 3) {
        Write-UpdateLog 'Job is no longer pending; no files were changed.'
        exit 0
    }

    $phase = 'checking-support-files'
    $runtimeScript = Join-Path $PSScriptRoot 'update-runtime.ps1'
    $toolsScript = Join-Path $root 'public-tools.ps1'
    foreach ($path in @($runtimeScript, $toolsScript)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw ('Required update support script is missing or unreadable: ' + $path)
        }
    }
    Start-Sleep -Seconds 4
    . $runtimeScript

    $phase = 'stopping-receiver'
    Write-UpdateLog 'Stopping Receiver before replacing any application files.'
    Stop-ThreadleReceiver -Root $root
    $stopped = $true

    if (Test-Path -LiteralPath (Join-Path $job 'previous.json')) {
        $phase = 'recovering-interrupted-update'
        Write-UpdateLog 'Recovering the completed backup of an interrupted update.'
        Invoke-UpdateDriver -Action 'restore' | Out-Null
        . $toolsScript
        Restart-ReceiverChecked -Public
        Write-UpdateLog 'Previous file hashes and Receiver response verified.'
        exit 0
    }

    $phase = 'applying-files'
    Write-UpdateLog 'Receiver stopped. Applying verified changed files.'
    Invoke-UpdateDriver -Action 'apply' | Out-Null

    $phase = 'restarting-receiver'
    Write-UpdateLog 'Starting updated Receiver and checking its response.'
    . $toolsScript
    Restart-ReceiverChecked -Public
    $detail = 'Application file hashes and Receiver response verified. Discord commands synchronize automatically in the running service.'
    Invoke-UpdateDriver -Action 'success' -Detail $detail | Out-Null
    Write-UpdateLog $detail
} catch {
    $failure = $_.Exception.Message
    try { Write-UpdateLog ('ERROR phase=' + $phase + ' identity=' + [Security.Principal.WindowsIdentity]::GetCurrent().Name + ' message=' + $failure) } catch {}

    if (-not $stopped) {
        Try-MarkFailed ('Update stopped during ' + $phase + '; no application files were replaced. See apply.log.')
        throw
    }

    try {
        if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'update-runtime.ps1') -PathType Leaf) {
            . (Join-Path $PSScriptRoot 'update-runtime.ps1')
        }
        Stop-ThreadleReceiver -Root $root
        if (Test-Path -LiteralPath (Join-Path $job 'previous.json')) {
            Write-UpdateLog 'Restoring previous files while Receiver is stopped.'
            Invoke-UpdateDriver -Action 'restore' | Out-Null
        } else {
            Try-MarkFailed 'Update stopped before a completed backup; application files were not replaced.'
        }
        . (Join-Path $root 'public-tools.ps1')
        Restart-ReceiverChecked -Public
        Write-UpdateLog 'Recovery completed. Receiver response verified.'
    } catch {
        Write-UpdateLog ('RECOVERY ERROR: ' + $_.Exception.Message)
        Try-MarkFailed 'Recovery needs attention. Check apply.log and preserve the backup.'
        throw
    }
    exit 1
}
