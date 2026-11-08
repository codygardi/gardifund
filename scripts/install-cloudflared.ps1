[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolDirectory = Join-Path $ProjectRoot '.tools'
$TargetPath = Join-Path $ToolDirectory 'cloudflared.exe'
$PartialPath = Join-Path $ToolDirectory 'cloudflared.exe.part'
if (Test-Path -LiteralPath $TargetPath) {
    Write-Host 'Workspace cloudflared already exists; leaving it unchanged.'
    & $TargetPath --version
    exit $LASTEXITCODE
}
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/cloudflare/cloudflared/releases/latest' -Headers @{ 'User-Agent' = 'GARDiFUND-setup'; 'Accept' = 'application/vnd.github+json' }
$asset = @($release.assets | Where-Object { $_.name -eq 'cloudflared-windows-amd64.exe' })
if ($asset.Count -ne 1 -or $asset[0].browser_download_url -notmatch '^https://github\.com/cloudflare/cloudflared/releases/download/[^/]+/cloudflared-windows-amd64\.exe$' -or $asset[0].digest -notmatch '^sha256:[a-f0-9]{64}$') {
    throw 'Cloudflare release metadata did not match the expected executable and SHA-256 digest.'
}
New-Item -ItemType Directory -Path $ToolDirectory -Force | Out-Null
if (Test-Path -LiteralPath $PartialPath) { throw "A partial download already exists: $PartialPath" }
try {
    Invoke-WebRequest -Uri $asset[0].browser_download_url -OutFile $PartialPath -UseBasicParsing
    $actual = (Get-FileHash -LiteralPath $PartialPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $asset[0].digest.Substring(7) -or (Get-Item -LiteralPath $PartialPath).Length -ne $asset[0].size) {
        throw 'Cloudflare executable size or SHA-256 digest did not match the official release.'
    }
    Move-Item -LiteralPath $PartialPath -Destination $TargetPath
    & $TargetPath --version
    if ($LASTEXITCODE -ne 0) { throw 'The downloaded cloudflared executable did not start.' }
} finally {
    if (Test-Path -LiteralPath $PartialPath) { Remove-Item -LiteralPath $PartialPath }
}
