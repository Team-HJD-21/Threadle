function Test-ReceiverProcess {
    param($Process,[string]$Root,[string]$Python)
    if (-not $Process.ExecutablePath -or -not $Process.CommandLine) { return $false }
    $expectedPython=[IO.Path]::GetFullPath($Python)
    if (-not ([IO.Path]::GetFullPath($Process.ExecutablePath)).Equals($expectedPython,[StringComparison]::OrdinalIgnoreCase)) { return $false }
    $tokens=@([regex]::Matches($Process.CommandLine,'"([^"]*)"|(\S+)') | ForEach-Object { if ($_.Groups[1].Success) { $_.Groups[1].Value } else { $_.Groups[2].Value } })
    $script=[IO.Path]::GetFullPath((Join-Path $Root 'service.py'))
    $entryIndex=1
    if ($tokens.Count -gt 1 -and $tokens[1] -eq '-B') { $entryIndex=2 }
    if ($tokens.Count -le $entryIndex -or -not $tokens[$entryIndex].Equals($script,[StringComparison]::OrdinalIgnoreCase)) { return $false }
    $state=[IO.Path]::GetFullPath($Root).TrimEnd('\')
    $scriptFound=$false;$stateFound=$false
    for ($index=1;$index -lt $tokens.Count;$index++) {
        if ($tokens[$index].Equals($script,[StringComparison]::OrdinalIgnoreCase)) { $scriptFound=$true }
        if ($tokens[$index] -eq '--state-dir' -and $index+1 -lt $tokens.Count) {
            $value=[IO.Path]::GetFullPath($tokens[$index+1]).TrimEnd('\')
            if ($value.Equals($state,[StringComparison]::OrdinalIgnoreCase)) { $stateFound=$true }
        }
    }
    return ($scriptFound -and $stateFound)
}

function Get-ReceiverProcesses {
    param([string]$Root,[string]$Python)
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object { Test-ReceiverProcess -Process $_ -Root $Root -Python $Python })
}

function Stop-RemainingReceiverProcesses {
    param([string]$Root,[string]$Python)
    foreach ($candidate in @(Get-ReceiverProcesses -Root $Root -Python $Python)) {
        # Re-read identity immediately before stopping to avoid a recycled process ID.
        $current=Get-CimInstance Win32_Process -Filter ('ProcessId='+$candidate.ProcessId)
        if ($current -and $current.CreationDate -eq $candidate.CreationDate -and (Test-ReceiverProcess -Process $current -Root $Root -Python $Python)) {
            Write-Host ('Stopping remaining verified Receiver process pid='+$current.ProcessId)
            Stop-Process -Id $current.ProcessId -Force -ErrorAction Stop
        }
    }
}
