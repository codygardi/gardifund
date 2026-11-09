[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkerName = 'gardifund'
$WorkerSubdomain = 'codygardi22'
$ViewerName = 'gardifund-view'
$ViewerPort = 8766

try {
    $view = Invoke-RestMethod -Uri "http://127.0.0.1:$ViewerPort/view/state" -TimeoutSec 5
} catch {
    throw 'Start GARDiFUND in public mode before enabling sharing.'
}
if ($view.access_scope -ne 'remote' -or $view.connection.auth_mode -ne 'public') {
    throw 'Set GARDIFUND_AUTH_MODE=public in .env and restart the GARDiFUND task.'
}

$ToolDirectory = Join-Path $ProjectRoot '.tools'
$TokenFile = Join-Path $ToolDirectory 'viewer-tunnel.token'
if (-not (Test-Path -LiteralPath (Join-Path $ToolDirectory 'cloudflared.exe')) -and
    -not (Get-Command cloudflared.exe -CommandType Application -ErrorAction SilentlyContinue)) {
    & (Join-Path $PSScriptRoot 'install-cloudflared.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install cloudflared.' }
}

function Protect-LocalPath([string]$Path, [switch]$Directory) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)
        $inheritance = if ($Directory) { 'ContainerInherit, ObjectInherit' } else { 'None' }
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $identity.User, 'FullControl', $inheritance, 'None', 'Allow')
        $acl.SetAccessRule($rule)
        Set-Acl -LiteralPath $Path -AclObject $acl
    } finally {
        $identity.Dispose()
    }
}

function Invoke-Wrangler {
    & npx.cmd --yes wrangler@4.158.0 @args
    if ($LASTEXITCODE -ne 0) { throw 'Wrangler did not complete the requested operation.' }
}

function Invoke-Cloudflare([string]$Path, [string]$Method = 'Get', $Body = $null, [switch]$AllowMissing) {
    $request = @{
        Uri = 'https://api.cloudflare.com/client/v4' + $Path
        Method = $Method
        Headers = @{ Authorization = 'Bearer ' + $CloudflareToken }
        TimeoutSec = 30
        MaximumRedirection = 0
    }
    if ($null -ne $Body) {
        $request.ContentType = 'application/json'
        $request.Body = $Body | ConvertTo-Json -Depth 6 -Compress
    }
    try {
        $response = Invoke-RestMethod @request
    } catch {
        $status = [int]$_.Exception.Response.StatusCode
        if ($AllowMissing -and $status -eq 404) { return }
        throw "Cloudflare request failed for $Method $Path (HTTP $status)."
    }
    if (-not $response.success) { throw "Cloudflare declined $Method $Path." }
    $response.result
}

$SetupDirectory = Join-Path $ToolDirectory ('cloudflare-setup-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $SetupDirectory -Force | Out-Null
$settings = @{
    XDG_CONFIG_HOME = $SetupDirectory
    NPM_CONFIG_CACHE = (Join-Path $SetupDirectory 'npm')
    WRANGLER_SEND_METRICS = 'false'
    CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV = 'false'
    CLOUDFLARE_API_TOKEN = $null
    CLOUDFLARE_API_KEY = $null
    CLOUDFLARE_ACCOUNT_ID = $null
    GARDIFUND_VPC_SERVICE_ID = $null
    GARDIFUND_WORKER_NAME = $WorkerName
}
$previous = @{}
$loggedIn = $false
Push-Location -LiteralPath $SetupDirectory
try {
    Protect-LocalPath $SetupDirectory -Directory
    foreach ($name in $settings.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    }

    Write-Host 'Approve Wrangler in the Cloudflare browser tab to connect this PC.'
    $browserOption = if ($NoBrowser) { '--browser=false' } else { '--browser' }
    Invoke-Wrangler login --device $browserOption --scopes account:read user:read workers_scripts:write connectivity:admin
    $loggedIn = $true
    try { $auth = Invoke-Wrangler auth token --json | ConvertFrom-Json } catch { throw 'Wrangler authorization did not return a usable session.' }
    if ($auth.type -ne 'oauth' -or -not $auth.token) { throw 'Cloudflare authorization is required.' }
    $CloudflareToken = $auth.token

    $accountId = $null
    foreach ($account in (Invoke-Cloudflare '/accounts?per_page=50')) {
        $subdomain = Invoke-Cloudflare "/accounts/$($account.id)/workers/subdomain" -AllowMissing
        if ($subdomain.subdomain -eq $WorkerSubdomain) {
            $accountId = $account.id
            break
        }
    }
    if (-not $accountId) { throw "Use the Cloudflare account for $WorkerSubdomain.workers.dev." }
    $accountPath = "/accounts/$accountId"
    $null = Invoke-Cloudflare "$accountPath/workers/scripts/$WorkerName/settings"
    $env:CLOUDFLARE_ACCOUNT_ID = $accountId

    $tunnels = @(Invoke-Cloudflare "$accountPath/cfd_tunnel?is_deleted=false&name=$ViewerName" |
        Where-Object name -EQ $ViewerName)
    if ($tunnels.Count -gt 1) { throw 'More than one Tunnel matches the viewer name.' }
    $tunnel = $tunnels | Select-Object -First 1
    if (-not $tunnel) {
        $tunnel = Invoke-Cloudflare "$accountPath/cfd_tunnel" Post @{ name = $ViewerName; config_src = 'cloudflare' }
    }
    if ($tunnel.config_src -ne 'cloudflare' -or -not $tunnel.id) { throw 'The viewer needs a remotely managed Tunnel.' }
    $tunnelPath = "$accountPath/cfd_tunnel/$($tunnel.id)"
    $configuration = Invoke-Cloudflare "$tunnelPath/configurations" -AllowMissing
    if ($configuration.config.ingress | Where-Object { $_.service -ne 'http_status:404' }) {
        throw 'The matching Tunnel already has published routes. Use a dedicated viewer Tunnel.'
    }

    $servicePath = "$accountPath/connectivity/directory/services"
    $services = @(Invoke-Cloudflare $servicePath | Where-Object name -EQ $ViewerName)
    if ($services.Count -gt 1) { throw 'More than one VPC Service matches the viewer name.' }
    $service = $services | Select-Object -First 1
    if (-not $service) {
        $service = Invoke-Cloudflare $servicePath Post @{
            name = $ViewerName
            type = 'http'
            http_port = $ViewerPort
            host = @{ ipv4 = '127.0.0.1'; network = @{ tunnel_id = $tunnel.id } }
        }
    }
    if ($service.type -ne 'http' -or $service.http_port -ne $ViewerPort -or
        $service.host.ipv4 -ne '127.0.0.1' -or $service.host.network.tunnel_id -ne $tunnel.id) {
        throw 'The existing viewer service points somewhere else. Its settings were left intact.'
    }
    $env:GARDIFUND_VPC_SERVICE_ID = $service.service_id
    $connectorToken = Invoke-Cloudflare "$tunnelPath/token"
    if ($connectorToken -isnot [string] -or $connectorToken -notmatch '^[A-Za-z0-9+/=_-]{40,}$') {
        throw 'Cloudflare did not return a usable connector token.'
    }
    $stagedToken = Join-Path $SetupDirectory 'viewer-tunnel.token'
    New-Item -ItemType File -Path $stagedToken | Out-Null
    Protect-LocalPath $stagedToken
    [IO.File]::WriteAllText($stagedToken, $connectorToken, [Text.Encoding]::ASCII)

    & node.exe (Join-Path $PSScriptRoot 'build-worker-config.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'The Worker configuration could not be generated.' }
    $workerConfig = Join-Path $ProjectRoot 'deploy\worker\wrangler.generated.json'
    Invoke-Wrangler deploy --config $workerConfig

    if (Test-Path -LiteralPath $TokenFile) {
        if (-not (Get-Acl -LiteralPath $TokenFile).AreAccessRulesProtected) {
            Protect-LocalPath $TokenFile
        }
        [IO.File]::Replace($stagedToken, $TokenFile, [NullString]::Value)
    } else {
        Move-Item -LiteralPath $stagedToken -Destination $TokenFile
    }
    Write-Host 'Worker deployed. Restart the GARDiFUND task to connect the public viewer.'
} finally {
    if ($loggedIn) {
        & npx.cmd --yes wrangler@4.158.0 logout *> $null
    }
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    Pop-Location
    $resolvedSetup = (Resolve-Path -LiteralPath $SetupDirectory).Path
    $resolvedTools = (Resolve-Path -LiteralPath $ToolDirectory).Path
    if ((Split-Path -Parent $resolvedSetup) -eq $resolvedTools) {
        Remove-Item -LiteralPath $resolvedSetup -Recurse -Force
    }
}
