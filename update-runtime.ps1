function Stop-ThreadleReceiver([string]$Root) {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $task = $folder.GetTask('TeamHJD-GitHubDiscord-Receiver')
    if ($task.State -in @(2,4)) { $task.Stop(0) }
    $deadline = (Get-Date).AddSeconds(30)
    while ($true) {
        $task = $folder.GetTask('TeamHJD-GitHubDiscord-Receiver')
        $listening = ([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()).Port -contains 8787
        if ($task.State -notin @(2,4) -and -not $listening) { return }
        if ((Get-Date) -gt $deadline) { throw 'Receiver task or port 8787 did not stop. No other process was killed.' }
        Start-Sleep -Milliseconds 300
    }
}
