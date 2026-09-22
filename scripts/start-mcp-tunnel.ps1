[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$Profile = "wt-advisor",

    [Parameter()]
    [string]$TunnelClientPath,

    [Parameter()]
    [string]$DatabasePath,

    [Parameter()]
    [ValidateRange(1024, 65535)]
    [int]$DashboardPort = 8765
)

$ErrorActionPreference = "Stop"

if (-not $TunnelClientPath) {
    $TunnelClientPath = if ($env:WTA_TUNNEL_CLIENT) {
        $env:WTA_TUNNEL_CLIENT
    }
    else {
        "D:\ChatGPT_MCP_Tunnel\tunnel-client.exe"
    }
}

if (-not (Test-Path -LiteralPath $TunnelClientPath -PathType Leaf)) {
    throw (
        "Tunnel client was not found at '$TunnelClientPath'. " +
        "Install it there, set WTA_TUNNEL_CLIENT, or pass -TunnelClientPath."
    )
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$resolvedDatabasePath = if ($DatabasePath) {
    [System.IO.Path]::GetFullPath($DatabasePath)
}
else {
    Join-Path $repositoryRoot "wt-advisor-live-acceptance.sqlite"
}
$dashboardExecutable = Join-Path $repositoryRoot ".venv\Scripts\wt-advisor.exe"
$dashboardUri = "http://127.0.0.1:$DashboardPort/"
$originalLocation = (Get-Location).Path
$originalDatabasePath = $env:WT_ADVISOR_DB
$dashboardProcess = $null

if (-not (Test-Path -LiteralPath $resolvedDatabasePath -PathType Leaf)) {
    throw "Advisor database was not found at '$resolvedDatabasePath'."
}

if (-not (Test-Path -LiteralPath $dashboardExecutable -PathType Leaf)) {
    throw (
        "Dashboard executable was not found at '$dashboardExecutable'. " +
        "Create the project virtual environment and install the package first."
    )
}

$portProbe = [System.Net.Sockets.TcpClient]::new()
try {
    $connectAttempt = $portProbe.BeginConnect("127.0.0.1", $DashboardPort, $null, $null)
    if ($connectAttempt.AsyncWaitHandle.WaitOne(250) -and $portProbe.Connected) {
        throw "Dashboard port 127.0.0.1:$DashboardPort is already in use."
    }
}
finally {
    $portProbe.Dispose()
}

try {
    Set-Location -LiteralPath $repositoryRoot
    $env:WT_ADVISOR_DB = $resolvedDatabasePath
    $dashboardProcess = Start-Process `
        -FilePath $dashboardExecutable `
        -ArgumentList @("dashboard", "--port", $DashboardPort) `
        -WindowStyle Hidden `
        -PassThru

    $dashboardReady = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ($dashboardProcess.HasExited) {
            throw "Dashboard exited before becoming ready (exit code $($dashboardProcess.ExitCode))."
        }

        try {
            $null = Invoke-WebRequest -Uri $dashboardUri -UseBasicParsing -TimeoutSec 1
            $dashboardReady = $true
            break
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }

    if (-not $dashboardReady) {
        throw "Dashboard did not become ready at '$dashboardUri'."
    }

    & $TunnelClientPath run --profile $Profile
    exit $LASTEXITCODE
}
finally {
    if ($null -ne $dashboardProcess -and -not $dashboardProcess.HasExited) {
        Stop-Process -Id $dashboardProcess.Id -ErrorAction SilentlyContinue
    }
    $env:WT_ADVISOR_DB = $originalDatabasePath
    Set-Location -LiteralPath $originalLocation
}
