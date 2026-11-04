function Wait-ReceiverHealthy {
    param(
        [Parameter(Mandatory=$true)][string]$HostName,
        [ValidateRange(1,120)][int]$StableChecks=2,
        [ValidateRange(0,300)][int]$TimeoutSeconds=60,
        [ValidateRange(1,10000)][int]$IntervalMilliseconds=1000
    )
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $consecutive=0
    do {
        try {
            $health=Invoke-WebRequest 'http://127.0.0.1:8787/health' -UseBasicParsing -TimeoutSec 2
            $page=Invoke-WebRequest 'http://127.0.0.1:8787/connect' -Headers @{Host=$HostName} -UseBasicParsing -TimeoutSec 2
            if ($health.StatusCode -eq 200 -and $health.Content.Trim() -eq 'receiver-ready' -and $page.StatusCode -eq 200) {
                $consecutive++
                if ($consecutive -ge $StableChecks) { return }
            } else { $consecutive=0 }
        } catch { $consecutive=0 }
        if ([DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Milliseconds $IntervalMilliseconds
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Receiver did not remain ready; deployment is not healthy.'
}
