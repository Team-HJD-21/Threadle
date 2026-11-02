$ErrorActionPreference = 'Stop'
function Initialize-LocalCredentials {
    Add-Type -AssemblyName System.Security
    $credentials = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'credentials.json') -Raw | ConvertFrom-Json
    foreach ($entry in $credentials.PSObject.Properties) {
        $bytes = [Security.Cryptography.ProtectedData]::Unprotect(
            [Convert]::FromBase64String($entry.Value), $null,
            [Security.Cryptography.DataProtectionScope]::LocalMachine)
        try { [Environment]::SetEnvironmentVariable($entry.Name, [Text.Encoding]::UTF8.GetString($bytes), 'Process') }
        finally { [Array]::Clear($bytes, 0, $bytes.Length) }
    }
    $env:PYTHONPATH = (Join-Path $PSScriptRoot 'vendor-public') + ';' + (Join-Path $PSScriptRoot 'vendor') + ';' + (Join-Path $PSScriptRoot 'vendor-presence-v1')
}
function Clear-LocalCredentials {
    foreach ($name in @('DISCORD_BOT_TOKEN','GITHUB_TOKEN','WEBHOOK_SECRET','GITHUB_APP_ID',
                         'GITHUB_APP_PRIVATE_KEY_FILE','GITHUB_APP_WEBHOOK_SECRET','ONBOARDING_ENABLED',
                         'PUBLIC_BASE_URL','DISCORD_CLIENT_ID','DISCORD_CLIENT_SECRET','GITHUB_APP_CLIENT_ID',
                         'GITHUB_APP_CLIENT_SECRET','GITHUB_APP_SLUG','CONNECT_SESSION_KEY','DISCORD_PUBLIC_KEY')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}
