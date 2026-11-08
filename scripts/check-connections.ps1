[CmdletBinding()]
param([uri]$RemoteUrl)
$ErrorActionPreference = 'Stop'

function Get-EndpointStatus([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -MaximumRedirection 0 -TimeoutSec 15 -ErrorAction Stop
        return [int]$response.StatusCode
    } catch {
        if ($null -ne $_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 'unreachable'
    }
}

Write-Host ('Local health: ' + (Get-EndpointStatus 'http://127.0.0.1:8765/healthz'))
Write-Host ('Local workspace: ' + (Get-EndpointStatus 'http://127.0.0.1:8765/api/state'))
Write-Host ('Read-only origin without JWT: ' + (Get-EndpointStatus 'http://127.0.0.1:8766/view/state') + ' (expected 401 or 503)')
if ($RemoteUrl) {
    if ($RemoteUrl.Scheme -ne 'https') { throw 'Use the HTTPS hostname protected by Cloudflare Access.' }
    $RemoteOrigin = $RemoteUrl.GetLeftPart([System.UriPartial]::Authority)
    Write-Host ('Anonymous WAN request: ' + (Get-EndpointStatus $RemoteOrigin) + ' (expect an Access sign-in redirect or denial)')
    Write-Host 'Open the hostname from another network, complete MFA, and verify Overview and Charts load.'
    Write-Host 'Investing, News, Database, Settings, Shadow reset, and all /api/* controls must remain unavailable remotely.'
} else {
    Write-Host 'To check a configured hostname: .\scripts\check-connections.ps1 -RemoteUrl https://fund.example.com'
}
