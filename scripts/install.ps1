[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
npm ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
if (-not (Test-Path -LiteralPath '.env')) {
    $initialConfig = @'
# Add the Tradier production key. Use scripts/set-setup-password.py for the Live lock.
PRODUCTION_API_KEY=
SETUPS_PASSWORD_HASH=

# Optional private Cloudflare viewing.
GARDIFUND_AUTH_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=
CF_ACCESS_AUD=
GARDIFUND_PRIVATE_VIEW_IP=
'@
    New-Item -ItemType File -Path '.env' -Value ($initialConfig + [Environment]::NewLine) | Out-Null
    Write-Host 'Created a blank .env. Set the Tradier key and any Cloudflare Access settings locally.'
} else {
    Write-Host 'Existing .env preserved.'
}
Write-Host 'Installation complete. For loopback development run .\scripts\start.ps1 -LocalDev.'
