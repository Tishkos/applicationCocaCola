# Plant Collector as a desktop application.
#
# Opens the dashboard in its own window - no browser frame, no address bar, nothing that says
# "localhost". First choice: the native window in app\desktop.py (pywebview on Edge WebView2,
# which every Windows 10/11 has). If the pywebview package is not installed yet, the fallback is
# the Edge or Chrome that is already on the PC in app mode (also a frameless window).
# If the collector is not running it is started first (real plant, read-only, the same as
# START-PLANT.bat), and the window shows the dashboard as soon as it answers.
#
# Clicked several times in a row, it still starts one window: the first launcher holds a mutex
# until its window is up and the others end at once; the window itself (desktop.py) is
# single-instance too, so a later click only brings it to the front.
#
# Made for the desktop shortcut that app\Install-DesktopShortcut.ps1 creates. By hand:
#   powershell -ExecutionPolicy Bypass -File app\launch.ps1          (-Demo for the demo plant)
param(
    [int]$Port = 8080,
    [switch]$Demo,        # start the demo plant (config\site.demo.yaml, simulated real plant) instead of the real one
    [switch]$NoStart      # only open the window; never start the collector
)
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---- 0. one launcher at a time -------------------------------------------------------------------
$mutex = New-Object System.Threading.Mutex($false, "Local\CCI.PlantCollector.launch")
try { $got = $mutex.WaitOne(0) } catch { $got = $true }     # an abandoned mutex is still acquired
if (-not $got) { exit 0 }                                   # another click is already opening the window

function Get-Url {
    $u = "http://localhost:$Port/"
    $f = Join-Path $root "data\dashboard.url"            # written by the collector when 8080 was taken
    if (Test-Path $f) { $t = (Get-Content $f | Select-Object -First 1); if ($t) { $u = $t.Trim() } }
    if (-not $u.EndsWith("/")) { $u += "/" }
    return $u
}
function Test-Alive($u) {
    try { Invoke-RestMethod ($u + "api/v1/health") -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}
function Start-NativeWindow($extra) {
    # Starts app\desktop.py. $true when the window is up (or was open already), $false when the
    # native window is not possible on this PC - the caller then opens the browser window.
    $venvPy  = Join-Path $root ".venv\Scripts\python.exe"
    $venvPyw = Join-Path $root ".venv\Scripts\pythonw.exe"
    $okMark  = Join-Path $root ".venv\.desktop-ok"
    if (-not ((Test-Path $venvPyw) -and (Test-Path $venvPy))) { return $false }
    if (-not (Test-Path $okMark)) {
        # First time only (takes a few seconds): can this Python load the window packages? The
        # answer is remembered in .venv\.desktop-ok, so the next clicks skip straight to the window.
        & $venvPy -c "import webview, clr" 2>$null
        if ($LASTEXITCODE -ne 0) {
            # not installed yet (an older installation): put the packages in from the wheels folder, once
            Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$root\scripts\run.ps1`"", "-InstallOnly") -WindowStyle Minimized -Wait
            & $venvPy -c "import webview, clr" 2>$null
            if ($LASTEXITCODE -ne 0) { return $false }
        }
        New-Item -ItemType File -Force $okMark | Out-Null
    }
    $a = @("`"$root\app\desktop.py`"", "--port", $Port) + $extra
    $p = Start-Process $venvPyw -ArgumentList $a -WorkingDirectory $root -PassThru
    if (-not $p) { return $false }
    $null = $p.Handle                                      # so that ExitCode is readable after it ends
    for ($i = 0; $i -lt 24; $i++) {                        # watch it for 6 s
        Start-Sleep -Milliseconds 250
        if ($p.HasExited) {
            if ($p.ExitCode -eq 0) { return $true }        # ended on purpose: the window was open already
            Remove-Item $okMark -Force                     # died at once: packages broken - browser window instead
            return $false
        }
    }
    return $true
}

# ---- 1. native window (preferred) -------------------------------------------------------------
$extra = @()
if ($Demo) { $extra += "--demo" }
if ($NoStart) { $extra += "--no-start" }
if (Start-NativeWindow $extra) { exit 0 }

# ---- 2. fallback: start the collector here, then Edge/Chrome in app mode ----------------------------
$url = Get-Url
if (-not (Test-Alive $url) -and -not $NoStart) {
    # Same start as START-PLANT.bat / START.bat, without opening a browser tab: registers the
    # Windows task (asks for administrator rights once) or, if refused, runs in a window.
    # This first start also installs the pywebview package from the wheels folder, so the
    # next click opens the native window.
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$root\scripts\run.ps1`"", "-NoBrowser", "-Port", $Port)
    if (-not $Demo) { $startArgs += "-Plant" }
    Start-Process powershell -ArgumentList $startArgs -WindowStyle Minimized
    for ($i = 0; $i -lt 180; $i++) {                      # up to 3 min: the first start installs packages
        Start-Sleep -Seconds 1
        $url = Get-Url
        if (Test-Alive $url) { break }
    }
    # packages are in now? then the native window after all
    if (Start-NativeWindow @("--no-start")) { exit 0 }
}

$candidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$browser = $null
foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { $browser = $c; break } }
if ($browser) {
    $profile = Join-Path $root "data\appwindow-browser"
    New-Item -ItemType Directory -Force $profile | Out-Null
    Start-Process $browser -ArgumentList @(
        "--app=$url", "--user-data-dir=`"$profile`"", "--window-size=1400,900",
        "--no-first-run", "--no-default-browser-check", "--disable-features=TranslateUI"
    )
} else {
    Start-Process $url                                   # no Edge/Chrome: the default browser
}
