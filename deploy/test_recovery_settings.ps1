param([Parameter(Mandatory=$true)][string]$TemporaryRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'recovery_settings.ps1')
foreach ($name in @('recovery_settings.ps1','deploy.ps1')) {
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name),[ref]$null,[ref]$parseErrors) | Out-Null
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
}
$launcher = Join-Path $TemporaryRoot 'receiver.ps1'
[IO.File]::WriteAllText($launcher,'exit $LASTEXITCODE')
$action = [pscustomobject]@{Path='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe';Arguments=('-NoProfile -File "' + $launcher + '"')}
$actions = [pscustomobject]@{Count=1;Row=$action}
$actions | Add-Member ScriptMethod Item { param($index) if ($index -ne 1) { throw 'Unexpected action index' }; return $this.Row }
$definition = [pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5};Actions=$actions;Settings=[pscustomobject]@{ExecutionTimeLimit='PT0S';RestartInterval='';RestartCount=0};Triggers='preserve-triggers'}
$task = [pscustomobject]@{Definition=$definition;Xml='<Task>original</Task>'}
$folder = [pscustomobject]@{Task=$task;RegisterCount=0}
$folder | Add-Member ScriptMethod GetTask { param($name) return $this.Task }
$folder | Add-Member ScriptMethod RegisterTaskDefinition { param($name,$definition,$flags,$user,$password,$logon,$sddl) if ($flags -ne 4 -or $user -ne 'SYSTEM' -or $logon -ne 5) { throw 'Unexpected registration scope' }; if ($definition.Settings.RestartCount -lt 1 -or $definition.Settings.RestartCount -gt 255) { throw 'Restart count exceeds the documented unsigned-byte schema' }; $this.RegisterCount++; return $this.Task }
Test-ReceiverRecoveryTask -Task $task -Root $TemporaryRoot
if ($folder.RegisterCount -ne 0) { throw 'Validation mutated task settings' }
$beforeArguments=$action.Arguments
$backup=Join-Path $TemporaryRoot 'task-before.xml'
Enable-ReceiverRecovery -Folder $folder -TaskName 'TeamHJD-GitHubDiscord-Receiver' -Root $TemporaryRoot -BackupFile $backup
if ($definition.Settings.ExecutionTimeLimit -ne 'PT0S' -or $action.Arguments -ne $beforeArguments -or $definition.Triggers -ne 'preserve-triggers') { throw 'Unrelated task settings changed' }
if ([IO.File]::ReadAllText($backup) -ne $task.Xml) { throw 'Original task definition not backed up' }
[IO.File]::WriteAllText($launcher,'Write-Output done')
$rejected=$false
try { Test-ReceiverRecoveryTask -Task $task -Root $TemporaryRoot } catch { $rejected=$true }
if (-not $rejected) { throw 'Launcher without explicit failure exit code was accepted' }
if ($folder.RegisterCount -ne 1) { throw 'Rejected launcher changed task settings' }
Write-Host 'RECOVERY_SETTINGS_PASS: retry settings, unlimited runtime, action/trigger preservation, backup and unsafe-launcher rejection.'
