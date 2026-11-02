#Requires -RunAsAdministrator
param([string]$Root = (Join-Path $env:ProgramData 'TeamHJD\GitHubDiscord'), [string]$Package)
$ErrorActionPreference = 'Stop'
$arguments = @{ Root = $Root }
if ($Package) { $arguments.Package = $Package }
& (Join-Path $PSScriptRoot 'repair-update.ps1') @arguments
