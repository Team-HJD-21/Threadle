param([Parameter(Mandatory=$true)][string]$TemporaryRoot)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'receiver_process.ps1')
foreach ($name in @('receiver_process.ps1','deploy.ps1')) {
    $errors=$null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name),[ref]$null,[ref]$errors) | Out-Null
    if ($errors.Count) { throw ($errors | Out-String) }
}
$python='C:\Program Files\Python\python.exe'
$entry=Join-Path $TemporaryRoot 'service.py'
$good=[pscustomobject]@{ProcessId=991;CreationDate='original';ExecutablePath=$python;CommandLine=('"'+$python+'" -B "'+$entry+'" --state-dir "'+$TemporaryRoot+'"')}
if (-not (Test-ReceiverProcess $good $TemporaryRoot $python)) { throw 'Expected receiver rejected' }
foreach ($line in @(
    ('"'+$python+'" -B "'+$entry+'" --state-dir "'+$TemporaryRoot+'-other"'),
    ('"'+$python+'" -B "'+(Join-Path $TemporaryRoot 'admin.py')+'" "'+$entry+'" --state-dir "'+$TemporaryRoot+'"'),
    ('"'+$python+'" -c "print(1)" "'+$entry+'" --state-dir "'+$TemporaryRoot+'"'),
    ('"'+$python+'" -B "'+$entry+'"')
)) {
    $other=[pscustomobject]@{ExecutablePath=$python;CommandLine=$line}
    if (Test-ReceiverProcess $other $TemporaryRoot $python) { throw 'Unrelated Python accepted' }
}
$wrongPython=[pscustomobject]@{ExecutablePath='C:\Other\python.exe';CommandLine=$good.CommandLine}
if (Test-ReceiverProcess $wrongPython $TemporaryRoot $python) { throw 'Other Python accepted' }
$script:stopped=@();$script:recycled=$false
function Get-CimInstance {
    param($ClassName,$Filter)
    if ($Filter -like 'ProcessId=*' -and $script:recycled) { return [pscustomobject]@{ProcessId=991;CreationDate='new';ExecutablePath=$python;CommandLine=$good.CommandLine} }
    return $good
}
function Stop-Process { param($Id,[switch]$Force,$ErrorAction) $script:stopped+= $Id }
Stop-RemainingReceiverProcesses $TemporaryRoot $python
if ($script:stopped.Count -ne 1 -or $script:stopped[0] -ne 991) { throw 'Verified process was not stopped' }
$script:stopped=@();$script:recycled=$true
Stop-RemainingReceiverProcesses $TemporaryRoot $python
if ($script:stopped.Count -ne 0) { throw 'Recycled process ID was stopped' }
$listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0)
try {
    $listener.Start()
    $probePort=$listener.LocalEndpoint.Port
    if (-not (Test-ReceiverListening -Port $probePort)) { throw 'Real listening endpoint was missed' }
    $listener.Stop()
    if (Test-ReceiverListening -Port $probePort) { throw 'Closed endpoint was reported as listening' }
} finally { $listener.Stop() }
Write-Host 'RECEIVER_PROCESS_PASS: exact runtime/entry/state scope, recycled PID rejection and actual TCP open/closed probes; no real process stopped.'
