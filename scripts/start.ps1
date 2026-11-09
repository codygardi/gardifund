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

$TunnelTokenFile = Join-Path $ProjectRoot '.tools\viewer-tunnel.token'
if (-not $LocalDev -and $RemotePort -eq 8766 -and (Test-Path -LiteralPath $TunnelTokenFile -PathType Leaf)) {
    $Connector = Join-Path $ProjectRoot '.tools\cloudflared.exe'
    if (-not (Test-Path -LiteralPath $Connector -PathType Leaf)) {
        $Connector = Get-Command cloudflared.exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty Source
    }
    if ($Connector) {
        try {
            $TunnelArguments = 'tunnel --no-autoupdate --protocol quic run --token-file "{0}"' -f $TunnelTokenFile
            Start-Process -FilePath $Connector -ArgumentList $TunnelArguments -WindowStyle Hidden
        } catch {
            Write-Warning 'The public viewer connector could not start. Check the Cloudflare Tunnel setup.'
        }
    } else {
        Write-Warning 'The public viewer needs cloudflared. Run .\scripts\install-cloudflared.ps1.'
    }
}

Write-Host "Local workspace: http://127.0.0.1:$Port/"
& '.\.venv\Scripts\python.exe' -m backend.server
exit $LASTEXITCODE
