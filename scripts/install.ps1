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
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from the example. Add the production key and Cloudflare Access settings; use scripts/set-setup-password.py for the Live password.'
} else {
    Write-Host 'Existing .env preserved.'
}
Write-Host 'Installation complete. For loopback development run .\scripts\start.ps1 -LocalDev.'
