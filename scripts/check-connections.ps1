[CmdletBinding()]
param([uri]$RemoteUrl)
$ErrorActionPreference = 'Stop'

if ($RemoteUrl -and ($RemoteUrl.Scheme -ne 'https' -or $RemoteUrl.UserInfo -or $RemoteUrl.Query -or $RemoteUrl.Fragment -or $RemoteUrl.AbsolutePath -ne '/')) {
    throw 'Use the HTTPS root of the Worker protected by Cloudflare Access.'
}

Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$handler.UseCookies = $false
$handler.UseProxy = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(10)

function Get-Endpoint([string]$Url, [switch]$ReadJson) {
    try {
        $response = $client.GetAsync($Url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    } catch {
        return @{ Status = 'unreachable'; MediaType = ''; Data = $null; Location = $null }
    }
    try {
        $mediaType = $response.Content.Headers.ContentType.MediaType
        $data = $null
        if ($ReadJson -and $response.IsSuccessStatusCode -and $mediaType -eq 'application/json') {
            try {
                $data = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
            } catch {
                $data = $null
            }
        }
        return @{ Status = [int]$response.StatusCode; MediaType = $mediaType; Data = $data; Location = $response.Headers.Location }
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

try {
    $health = Get-Endpoint 'http://127.0.0.1:8765/healthz'
    $state = Get-Endpoint 'http://127.0.0.1:8765/api/state' -ReadJson
    Write-Host ('Local health: ' + $health.Status)
    Write-Host ('Local workspace: ' + $state.Status)
    if ($health.Status -ne 200 -or $health.MediaType -ne 'application/json' -or
        $state.Status -ne 200 -or $state.Data.access_scope -ne 'local') {
        Write-Host 'LOCAL WORKSPACE UNAVAILABLE. Start GARDiFUND and check the JSON endpoints on port 8765.'
        exit 1
    }
    Write-Host 'Local connection checks passed.'

    $connection = $state.Data.connection
    if ($connection.auth_mode -ne 'cloudflare' -or -not $connection.access_configured) {
        Write-Host 'Read-only sharing awaits the Worker Access team URL and audience in the local environment.'
        if ($RemoteUrl) { exit 1 }
        exit 0
    }
    $view = Get-Endpoint ('http://127.0.0.1:' + $connection.remote_port + '/scope')
    Write-Host ('Viewing listener without JWT: ' + $view.Status)
    if ($view.Status -ne 401 -or $view.MediaType -ne 'application/json') {
        Write-Host 'VIEWING LISTENER NOT READY. Check the Access settings and the viewing port.'
        exit 1
    }
    if ($RemoteUrl) {
        $page = Get-Endpoint $RemoteUrl.AbsoluteUri
        $scope = Get-Endpoint ([uri]::new($RemoteUrl, '/scope').AbsoluteUri)
        Write-Host ('Anonymous dashboard: ' + $page.Status)
        Write-Host ('Anonymous dashboard scope: ' + $scope.Status)
        if (-not (Test-AnonymousDenial $page) -or -not (Test-AnonymousDenial $scope)) {
            Write-Host 'WAN CHECK FAILED. Deploy the read-only Worker with the VPC Service and require Access sign-in.'
            exit 1
        }
        Write-Host 'Anonymous checks passed. Verify MFA and updates from a browser on another network.'
    }
} finally {
    $client.Dispose()
}
exit 0
