# Receiver stop verification during deployment

The beta.19 main deployment failed before file replacement because Stop-Receiver did not observe both the scheduled task stopped and port 8787 closed within 40 seconds. The existing log cannot distinguish a remaining Python child, a task restart race or an unrelated listener. CI verification succeeded; the site remained beta.18.

During an actual deployment, temporarily disable the Receiver scheduled task, request its existing stop and give it five seconds. If verified Receiver Python processes remain, stop only processes using the configured Python executable, the exact runtime service.py entry point and the exact --state-dir. Re-read creation time and process identity before stopping. A different program on 8787 is never terminated. Continue to require the task stopped, no verified Receiver processes, and the port closed before changing files.

Restore the previous task enabled state when starting and in the deployment finally block. Existing backup-before-apply, task settings recovery and file rollback remain in place. Timeout diagnostics now include task state, port status and verified process count without command lines or credentials.

The new PowerShell check uses mocked process enumeration and termination, including unrelated Python commands and a recycled PID. It never stops a real process. Windows CI invokes this check alongside existing recovery-settings validation.
