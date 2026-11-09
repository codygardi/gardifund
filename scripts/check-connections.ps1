[CmdletBinding()]
param([switch]$RequirePrivateView)
$ErrorActionPreference = 'Stop'

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
        return @{ Status = 'unreachable'; MediaType = ''; Data = $null }
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
        return @{ Status = [int]$response.StatusCode; MediaType = $mediaType; Data = $data }
    } finally {
        $response.Dispose()
    }
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

    $privateIp = $state.Data.connection.private_view_ip
    if (-not $privateIp -or -not $state.Data.connection.access_configured) {
        Write-Host 'Private viewing awaits the private IP, Access team URL and audience.'
        if ($RequirePrivateView) { exit 1 }
        exit 0
    }
    $origin = 'http://' + $privateIp
    $view = Get-Endpoint ($origin + '/scope')
    Write-Host ('Private origin without JWT: ' + $view.Status)
    if ($view.Status -ne 401 -or $view.MediaType -ne 'application/json') {
        Write-Host 'PRIVATE ORIGIN NOT READY. Check the Access settings, port 80 and the selected Windows IP.'
        exit 1
    }
    Write-Host 'Private origin rejected the anonymous request. Verify MFA and live updates from a device enrolled in Cloudflare One Client.'
} finally {
    $client.Dispose()
}
exit 0
