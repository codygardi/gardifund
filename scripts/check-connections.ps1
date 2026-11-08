[CmdletBinding()]
param([uri]$RemoteUrl)
$ErrorActionPreference = 'Stop'

if ($RemoteUrl -and $RemoteUrl.Scheme -ne 'https') {
    throw 'Use the HTTPS hostname protected by Cloudflare Access.'
}

Add-Type -AssemblyName System.Net.Http
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.AllowAutoRedirect = $false
$handler.UseCookies = $false
$client = New-Object System.Net.Http.HttpClient($handler)
$client.Timeout = [TimeSpan]::FromSeconds(15)

function Get-Endpoint([string]$Url) {
    try {
        $response = $client.GetAsync($Url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    } catch {
        return @{ Status = 'unreachable'; MediaType = ''; Location = $null }
    }
    try {
        return @{
            Status = [int]$response.StatusCode
            MediaType = $response.Content.Headers.ContentType.MediaType
            Location = $response.Headers.Location
        }
    } finally {
        $response.Dispose()
    }
}

function Test-AnonymousDenial($Probe) {
    if ($Probe.Status -in 401, 403) { return $true }
    $location = $Probe.Location
    return ($Probe.Status -in 302, 303, 307, 308) -and $location -and $location.IsAbsoluteUri -and
        $location.Scheme -eq 'https' -and $location.Host.EndsWith('.cloudflareaccess.com') -and
        $location.AbsolutePath.StartsWith('/cdn-cgi/access/login')
}

$failed = $false
try {
    $health = Get-Endpoint 'http://127.0.0.1:8765/healthz'
    $state = Get-Endpoint 'http://127.0.0.1:8765/api/state'
    $view = Get-Endpoint 'http://127.0.0.1:8766/view/state'
    Write-Host ('Local health: ' + $health.Status)
    Write-Host ('Local workspace: ' + $state.Status)
    Write-Host ('Read-only origin without JWT: ' + $view.Status)
    if ($health.Status -ne 200 -or $health.MediaType -ne 'application/json' -or
        $state.Status -ne 200 -or $state.MediaType -ne 'application/json') {
        Write-Host 'LOCAL WORKSPACE UNAVAILABLE. Start GARDiFUND and check the JSON endpoints on port 8765.'
        $failed = $true
    }
    if ($view.Status -notin 401, 503, 'unreachable') {
        Write-Host 'VIEW ORIGIN CHECK FAILED. Port 8766 should reject requests without a JWT.'
        $failed = $true
    }

    if ($RemoteUrl) {
        if ($view.Status -ne 401) {
            Write-Host 'VIEW ORIGIN NOT READY. Start GARDiFUND in Cloudflare mode with the Access team domain and audience.'
            $failed = $true
        }
        $remoteOrigin = $RemoteUrl.GetLeftPart([System.UriPartial]::Authority)
        $root = Get-Endpoint $remoteOrigin
        $scope = Get-Endpoint ($remoteOrigin + '/scope')
        Write-Host ('Anonymous WAN page: ' + $root.Status)
        Write-Host ('Anonymous WAN scope: ' + $scope.Status)
        if ($scope.Status -eq 200 -and $scope.MediaType -ne 'application/json') {
            Write-Host 'SYNC NOT CONNECTED. /scope returned a static page. Deploy the read-only gateway.'
            Write-Host 'See deploy/free-cloudflare.md for the Cloudflare Builds commands and VPC Service configuration.'
            $failed = $true
        } elseif (-not (Test-AnonymousDenial $root) -or -not (Test-AnonymousDenial $scope)) {
            Write-Host 'WAN CHECK FAILED. Both the page and /scope should redirect to Cloudflare Access or deny anonymous requests.'
            $failed = $true
        }
        if (-not $failed) {
            Write-Host 'Anonymous connection checks passed. From another network, complete MFA and verify Overview and Charts load and update.'
            Write-Host 'Confirm that Investing, News, Database, Settings, Shadow reset, and all /api/* controls are restricted to localhost.'
        }
    } elseif (-not $failed) {
        Write-Host 'Local connection checks passed.'
        Write-Host 'To check the Worker: .\scripts\check-connections.ps1 -RemoteUrl https://gardifund.codygardi22.workers.dev'
    }
} finally {
    $client.Dispose()
}
if ($failed) { exit 1 }
exit 0
