# CCI Plant Monitoring in Grafana, one click.
#
#   GRAFANA-DEMO.bat     simulated real plant (tools\grafana_demo_data.py -> data\grafana\plant-demo.sqlite)
#
# Starts Docker Desktop if it is not running, builds/starts the Grafana container (grafana\docker-compose.yml)
# and opens the dashboard in its own window (Edge app mode: no tabs, no address bar).
# The demo data generator runs in a minimized window; closing that window freezes the demo data.
param(
    [switch]$Demo,
    [switch]$Stop
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$url = "http://localhost:3000/d/cci-plant-monitoring/cci-plant-monitoring?orgId=1"

function Say($m) { Write-Host "[grafana] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[grafana] $m" -ForegroundColor Yellow }

if ($Stop) {
    docker compose -p cci-grafana -f grafana\docker-compose.yml down
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*grafana_demo_data.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Say "Grafana and the demo data generator stopped."
    exit 0
}

# ---- 1. Docker ----------------------------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed. Install Docker Desktop (https://www.docker.com/products/docker-desktop) and start again."
}
$ErrorActionPreference = "Continue"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Say "Starting Docker Desktop..."
    $dd = @("$env:ProgramFiles\Docker\Docker\Docker Desktop.exe", "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($dd) { Start-Process $dd }
    for ($i = 0; $i -lt 90; $i++) { Start-Sleep -Seconds 2; docker info *> $null; if ($LASTEXITCODE -eq 0) { break } }
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop did not start within 3 minutes." }
}
$ErrorActionPreference = "Stop"

# ---- 2. Data -------------------------------------------------------------------------------------
if ($Demo) {
    $env:PLANT_DB = "grafana/plant-demo.sqlite"
    $running = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*grafana_demo_data.py*" }
    if (-not $running) {
        $py = Join-Path $root ".venv\Scripts\python.exe"
        if (-not (Test-Path $py)) {
            Say "First run: installing Python packages..."
            & powershell -NoProfile -ExecutionPolicy Bypass -File "$root\scripts\run.ps1" -InstallOnly
        }
        Say "Starting the demo data generator (simulated real plant, 7 days of history first, about a minute)..."
        Start-Process $py -ArgumentList "`"$root\tools\grafana_demo_data.py`"" -WorkingDirectory $root -WindowStyle Minimized
        for ($i = 0; $i -lt 180; $i++) {
            if (Test-Path "data\grafana\plant-demo.sqlite") { if ((Get-Item "data\grafana\plant-demo.sqlite").LastWriteTime -gt (Get-Date).AddMinutes(-3)) { break } }
            Start-Sleep -Seconds 1
        }
    }
} else {
    $env:PLANT_DB = "telemetry.sqlite"
}

# ---- 3. Grafana ----------------------------------------------------------------------------------
Say "Starting Grafana (the first time builds the CCI image, a few minutes)..."
$ErrorActionPreference = "Continue"
docker compose -p cci-grafana -f grafana\docker-compose.yml up -d --build
$ErrorActionPreference = "Stop"
Write-Host "[grafana] Waiting for Grafana " -ForegroundColor Cyan -NoNewline
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    try { Invoke-RestMethod "http://localhost:3000/api/health" -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Write-Host "." -NoNewline; Start-Sleep -Seconds 1 }
}
Write-Host ""
if (-not $ok) { throw "Grafana did not answer on http://localhost:3000 - see: docker compose -p cci-grafana -f grafana\docker-compose.yml logs" }

# ---- 4. Window -----------------------------------------------------------------------------------
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
    $profile = Join-Path $root "data\grafana-window"
    New-Item -ItemType Directory -Force $profile | Out-Null
    Start-Process $edge -ArgumentList @("--app=$url", "--user-data-dir=`"$profile`"", "--window-size=1600,1000",
        "--no-first-run", "--no-default-browser-check")
} else {
    Start-Process $url
}
Say "CCI Plant Monitoring: $url"
