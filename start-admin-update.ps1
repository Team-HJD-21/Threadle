param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$JobId)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$job = Join-Path $root ('admin-updates\' + $JobId)
if (-not (Test-Path -LiteralPath (Join-Path $job 'package.zip'))) { throw 'Verified update package is missing.' }
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$definition = $scheduler.NewTask(0)
$definition.RegistrationInfo.Description = 'Apply one authenticated and signed Threadle update.'
$definition.Principal.UserId = 'SYSTEM'
$definition.Principal.LogonType = 5
$definition.Principal.RunLevel = 1
$definition.Settings.Enabled = $true
$definition.Settings.AllowDemandStart = $true
$definition.Settings.DisallowStartIfOnBatteries = $false
$definition.Settings.StopIfGoingOnBatteries = $false
$definition.Settings.ExecutionTimeLimit = 'PT10M'
$definition.Settings.MultipleInstances = 2
$trigger = $definition.Triggers.Create(8)
$trigger.Delay = 'PT30S'
$action = $definition.Actions.Create(0)
$action.Path = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $root 'apply-admin-update.ps1') + '" -JobId ' + $JobId
$action.WorkingDirectory = $root
$runtime = Get-Content -LiteralPath (Join-Path $root 'runtime.json') -Raw | ConvertFrom-Json
& $runtime.python -B (Join-Path $job 'driver.py') --root $root --job $JobId scheduled
if ($LASTEXITCODE -ne 0) { throw 'Update scheduling verification failed.' }
$task = $folder.RegisterTaskDefinition('TeamHJD-Threadle-Update', $definition, 6, 'SYSTEM', $null, 5)
$task.Run($null) | Out-Null
Write-Host 'Independent update task started.'
