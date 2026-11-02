function Test-ReceiverRecoveryTask {
    param($Task, [string]$Root)
    $definition = $Task.Definition
    if ($definition.Principal.LogonType -ne 5 -or $definition.Principal.UserId -notin @('SYSTEM','NT AUTHORITY\SYSTEM','S-1-5-18')) {
        throw 'Recovery configuration supports only the existing SYSTEM service task.'
    }
    if ($definition.Actions.Count -ne 1) { throw 'Receiver task must have exactly one existing action.' }
    $action = $definition.Actions.Item(1)
    if ([IO.Path]::GetFileName($action.Path) -notin @('powershell.exe','pwsh.exe')) { throw 'Receiver action is not the expected PowerShell launcher.' }
    $match = [regex]::Match($action.Arguments, '(?i)(?:^|\s)-File\s+(?:"([^"]+)"|''([^'']+)''|(\S+))')
    if (-not $match.Success) { throw 'Receiver launcher file could not be identified.' }
    $scriptPath = @($match.Groups[1].Value,$match.Groups[2].Value,$match.Groups[3].Value) | Where-Object { $_ } | Select-Object -First 1
    $scriptPath = [IO.Path]::GetFullPath($scriptPath)
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    if (-not [IO.Path]::GetDirectoryName($scriptPath).Equals($rootPath,[StringComparison]::OrdinalIgnoreCase)) { throw 'Receiver launcher is outside the service root.' }
    $scriptText = [IO.File]::ReadAllText($scriptPath)
    if ($scriptText -notmatch '(?im)^\s*exit\s+\$LASTEXITCODE\s*$') {
        throw 'Receiver launcher does not explicitly propagate the Python exit code. No task settings changed; inspect the launcher before enabling recovery.'
    }
}

function Enable-ReceiverRecovery {
    param($Folder, [string]$TaskName, [string]$Root, [string]$BackupFile)
    $task = $Folder.GetTask($TaskName)
    Test-ReceiverRecoveryTask -Task $task -Root $Root
    [IO.File]::WriteAllText($BackupFile,$task.Xml,(New-Object Text.UTF8Encoding($false)))
    $definition = $task.Definition
    $originalLimit = $definition.Settings.ExecutionTimeLimit
    $definition.Settings.RestartInterval = 'PT1M'
    $definition.Settings.RestartCount = 255
    $updated = $Folder.RegisterTaskDefinition($TaskName,$definition,4,'SYSTEM',$null,5,$null)
    if ($updated.Definition.Settings.RestartInterval -ne 'PT1M' -or $updated.Definition.Settings.RestartCount -ne 255 -or $updated.Definition.Settings.ExecutionTimeLimit -ne $originalLimit) {
        throw 'Receiver recovery settings did not verify; deployment will restore the previous task.'
    }
    Write-Host ('Receiver failure recovery enabled: 1 minute / 255 attempts; execution time limit preserved: ' + $originalLimit)
}
