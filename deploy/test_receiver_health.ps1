param([string]$TemporaryRoot)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'receiver_health.ps1')
$script:attempt=0
$script:badPage=$false
function Start-Sleep { param($Milliseconds) }
function Invoke-WebRequest {
    param($Uri,$Headers,[switch]$UseBasicParsing,$TimeoutSec)
    if ($Uri.EndsWith('/health')) {
        $script:attempt++
        return [pscustomobject]@{StatusCode=$script:codes[$script:attempt-1];Content='receiver-ready'}
    }
    if ($Headers.Host -ne 'threadle.example') { throw 'Expected Host header' }
    return [pscustomobject]@{StatusCode=$(if ($script:badPage) {503} else {200});Content='Page'}
}
# A transient healthy response followed by failure cannot complete the check.
$script:codes=@(200,200,503,200,200,200)
Wait-ReceiverHealthy -HostName 'threadle.example' -StableChecks 3
if ($script:attempt -ne 6) { throw 'Transient readiness was accepted' }
# Even a ready receiver is unhealthy if its real connection page is unavailable.
$script:attempt=0;$script:codes=@(200);$script:badPage=$true;$rejected=$false
try { Wait-ReceiverHealthy -HostName 'threadle.example' -StableChecks 1 -TimeoutSeconds 0 } catch { $rejected=$true }
if (-not $rejected) { throw 'Unavailable connection page was accepted' }
$script:badPage=$false;$script:attempt=0;$script:codes=@(503);$rejected=$false
try { Wait-ReceiverHealthy -HostName 'threadle.example' -StableChecks 1 -TimeoutSeconds 0 } catch { $rejected=$true }
if (-not $rejected) { throw 'Unready coordinator was accepted' }
Write-Host 'RECEIVER_HEALTH_PASS: sustained readiness required; readiness drops and unavailable pages rejected.'
