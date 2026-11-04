param(
    [string]$Root = 'C:\ProgramData\TeamHJD\GitHubDiscord',
    [Parameter(Mandatory = $true)][string]$Sender
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$address = [System.Net.Mail.MailAddress]::new($Sender)
if ($address.Address -cne $Sender -or $Sender -match '[\r\n]') { throw 'Enter an email address without a display name.' }
$credentialPath = Join-Path $Root 'credentials.json'
if (-not (Test-Path -LiteralPath $credentialPath -PathType Leaf)) { throw 'Existing Threadle credentials.json was not found.' }
$credentials = Get-Content -LiteralPath $credentialPath -Raw | ConvertFrom-Json
$secret = Read-Host 'Google Workspace app password (hidden; do not use your account password)' -AsSecureString
$pointer = [IntPtr]::Zero
$plainBytes = $null
try {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Replace(' ', '')
    if ($password -cnotmatch '^[a-zA-Z0-9]{16}$') { throw 'A Google app password contains 16 characters.' }
    $values = @{
        THREADLE_SMTP_HOST = 'smtp.gmail.com'
        THREADLE_SMTP_PORT = '465'
        THREADLE_SMTP_FROM = $Sender
        THREADLE_SMTP_USER = $Sender
        THREADLE_SMTP_PASSWORD = $password
    }
    foreach ($name in $values.Keys) {
        $plainBytes = [Text.Encoding]::UTF8.GetBytes($values[$name])
        try {
            $protected = [Security.Cryptography.ProtectedData]::Protect($plainBytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
            $credentials | Add-Member -NotePropertyName $name -NotePropertyValue ([Convert]::ToBase64String($protected)) -Force
        } finally { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
    }
    $backup = Join-Path $Root ('credentials-before-admin-mail-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffff') + '.json')
    Copy-Item -LiteralPath $credentialPath -Destination $backup
    $temporary = Join-Path $Root ('credentials-admin-mail-' + [Guid]::NewGuid().ToString('N') + '.json')
    [IO.File]::WriteAllText($temporary, ($credentials | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $credentialPath -Force
    Write-Host 'Encrypted SMTP settings saved. No email was sent.'
    Write-Host 'Restart the Threadle receiver after deployment, then enable the subscription in administration.'
} finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $secret.Dispose()
    Remove-Variable password,values,plainBytes,protected,credentials -ErrorAction SilentlyContinue
}
