[CmdletBinding()]
param(
    [switch]$LocalDev,
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [ValidateRange(1024, 65535)][int]$RemotePort = 8766
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe') -or -not (Test-Path -LiteralPath 'dist\index.html')) {
    throw 'Dependencies or the compiled UI are missing. Run .\scripts\install.ps1 first.'
}
. (Join-Path $PSScriptRoot 'job-control.ps1')
Enable-GardiFundProcessJob
if ($Port -eq $RemotePort) { throw 'Local and remote listener ports must differ.' }
$env:GARDIFUND_LOCAL_PORT = "$Port"
$env:GARDIFUND_REMOTE_PORT = "$RemotePort"
if ($LocalDev) {
    $env:GARDIFUND_AUTH_MODE = 'local'
}
Write-Host "Local workspace: http://127.0.0.1:$Port/"
& '.\.venv\Scripts\python.exe' -m backend.server
exit $LASTEXITCODE
