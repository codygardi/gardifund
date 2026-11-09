[CmdletBinding()]
param([uri]$RemoteUrl)
$ErrorActionPreference = 'Stop'

if ($RemoteUrl -and ($RemoteUrl.Scheme -ne 'https' -or $RemoteUrl.UserInfo -or $RemoteUrl.Query -or $RemoteUrl.Fragment -or $RemoteUrl.AbsolutePath -ne '/')) {
    throw 'Use the HTTPS root of the Worker.'
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
        return @{ Status = 'unreachable'; MediaType = ''; Data = $null; Location = $null; PagesOrigin = '' }
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
        $pagesOrigin = if ($response.Headers.Contains('Access-Control-Allow-Origin')) { $response.Headers.GetValues('Access-Control-Allow-Origin') -join ',' } else { '' }
        return @{ Status = [int]$response.StatusCode; MediaType = $mediaType; Data = $data; Location = $response.Headers.Location; PagesOrigin = $pagesOrigin }
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
    if ($connection.auth_mode -eq 'public') {
        $viewerBase = 'http://127.0.0.1:' + $connection.remote_port
        $view = Get-Endpoint ($viewerBase + '/view/state') -ReadJson
        $denied = Get-Endpoint ($viewerBase + '/api/state')
        Write-Host ('Public viewing listener: ' + $view.Status)
        if ($view.Status -ne 200 -or $view.Data.access_scope -ne 'remote' -or $denied.Status -ne 403) {
            Write-Host 'VIEWING LISTENER NOT READY. Check the local viewing port and restart GARDiFUND.'
            exit 1
        }
        if ($RemoteUrl) {
            $edge = Get-Endpoint ([uri]::new($RemoteUrl, '/view/state').AbsoluteUri) -ReadJson
            Write-Host ('Public Worker state: ' + $edge.Status)
            if (Test-AnonymousDenial $edge) {
                Write-Host 'The public Worker still requires access or denied the request. Check the gateway deployment and Access for this Worker.'
                exit 1
            }
            if ($edge.Status -ne 200 -or $edge.Data.access_scope -ne 'remote' -or
                $edge.Data.connection.auth_mode -ne 'public' -or $edge.PagesOrigin -ne 'https://codygardi.github.io') {
                Write-Host 'WAN VIEW UNAVAILABLE. Check the Worker deployment and its Tunnel VPC Service.'
                exit 1
            }
            $edgeDenied = Get-Endpoint ([uri]::new($RemoteUrl, '/api/state').AbsoluteUri)
            if ($edgeDenied.Status -ne 403) {
                Write-Host 'The Worker must deny local API routes.'
                exit 1
            }
            Write-Host 'The public read-only Worker is reachable from GitHub Pages.'
        }
        exit 0
    }
    if ($connection.auth_mode -ne 'cloudflare' -or -not $connection.access_configured) {
        Write-Host 'Set GARDIFUND_AUTH_MODE=public in the local environment to enable the Pages viewer.'
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
