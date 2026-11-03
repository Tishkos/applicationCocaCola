# One click: installs what is missing, registers the collector to start with Windows,
# starts it now and opens the dashboard. Safe to run again any time.
#
#   START.bat            demo of the real plant, simulated (config\site.demo.yaml)
#   START-PLANT.bat      real plant (config\site.plant.yaml), read-only polling
#   CHECK-PLANT.bat      one poll of the real plant, every point printed (no background task)
#   PROBE-PLANT.bat      which controllers answer on Modbus TCP at all (one read of one register each)
#   TRACE-PLANT.bat      fast read-only trace to data\trace (1 s master, 5 s SAU) in this window, Ctrl+C stops
#   SESSION.bat          the whole measuring session, one click: probe, check, trace in phases, report, zip, long run
#   RUN-IN-WINDOW.bat    run in the console instead (no Windows task); shows every message
#   STOP.bat             stop the background collector
#   UNINSTALL.bat        remove the Windows task (data stays in data\)
#
# The collector runs as a Windows scheduled task (at startup, as SYSTEM, restarts itself if it
# fails). Shutting the computer down is fine: it starts again with Windows, and the database
# in data\ keeps everything that was collected.
param(
    [switch]$Plant,
    [switch]$Check,
    [switch]$Trace,
    [switch]$Probe,
    [switch]$Session,
    [switch]$SessionDemo,
    [switch]$Foreground,
    [switch]$NoBrowser,
    [switch]$Elevated,
    [switch]$InstallOnly,  # Python, the virtual environment and the packages only - nothing started (app\launch.ps1 uses it)
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$TaskName = "PlantDataCollector"

function Say($m) { Write-Host "[collector] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[collector] $m" -ForegroundColor Yellow }

# ---- 1. Python ---------------------------------------------------------------
# Windows ships a fake "python" (WindowsApps) that only opens the Microsoft Store; ignore it, never stop on it.
function Test-Py($exe) {
    if (-not $exe -or -not (Test-Path $exe) -or $exe -match "WindowsApps") { return $false }
    try { $v = (& $exe --version 2>&1 | Out-String) } catch { return $false }
    return ($v -match "Python 3\.(1[0-9]|9)")
}
$pyExe = $null
$cands = @("$env:ProgramFiles\Python312\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")   # the wheels are for 3.12
$c = Get-Command python -ErrorAction SilentlyContinue
if ($c) { $cands += $c.Source }
foreach ($v in "313", "311", "310") {
    $cands += "$env:ProgramFiles\Python$v\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe"
}
foreach ($x in $cands) { if (Test-Py $x) { $pyExe = $x; break } }
if (-not $pyExe) {
    Say "Python 3 not found. Downloading it from python.org (about 25 MB, one time; slow internet takes a while)..."
    $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    $exe = Join-Path $env:TEMP "python-installer.exe"
    $ProgressPreference = "SilentlyContinue"            # the progress bar makes the download many times slower
    Invoke-WebRequest $url -OutFile $exe
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $all = if ($admin) { "1" } else { "0" }
    Say "Installing Python (quiet, about a minute)..."
    Start-Process $exe -ArgumentList "/quiet InstallAllUsers=$all PrependPath=1 Include_test=0" -Wait
    foreach ($x in "$env:ProgramFiles\Python312\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe") {
        if (Test-Py $x) { $pyExe = $x; break }
    }
    if (-not $pyExe) { throw "Python install failed - install Python 3.12 from python.org (tick 'Add to PATH') and start again" }
}
Say ("Python: " + (& $pyExe --version 2>&1 | Out-String).Trim() + "  (" + $pyExe + ")")

# ---- 2. Virtual environment + packages (first run only) --------------------------
if (-not (Test-Path ".venv")) {
    Say "Creating virtual environment..."
    & $pyExe -m venv .venv
}
$venvPy  = Join-Path $root ".venv\Scripts\python.exe"
$venvPyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$stamp = Join-Path $root ".venv\.installed"
$need = "import pymodbus, yaml, pydantic, fastapi, uvicorn, openpyxl, requests, xlrd"
if (-not (Test-Path $stamp) -or ((Get-Item requirements.txt).LastWriteTime -gt (Get-Item $stamp).LastWriteTime)) {
    $ErrorActionPreference = "Continue"                 # pip writes notices to stderr; judge by exit codes
    $ok = $false
    if (Test-Path "wheels\*.whl") {
        # first from the wheels folder in the repository: no internet needed (the site's line does not reach the package server)
        Say "Installing packages from the wheels folder (no internet needed)..."
        # only the wheels of this Python: "-cp312-cp312-" for 3.12, "-cp313-cp313-" for 3.13, and the pure ones
        $minor = (& $venvPy -c "import sys; print(sys.version_info[1])" | Out-String).Trim()
        $whl = @(Get-ChildItem "wheels\*.whl" | Where-Object { $_.Name -notmatch "-cp3\d+-cp3\d+-" -or $_.Name -match "-cp3$minor-cp3$minor-" } | ForEach-Object { $_.FullName })
        & $venvPy -m pip install --quiet --no-index --find-links wheels @whl
        & $venvPy -c $need 2>$null
        $ok = ($LASTEXITCODE -eq 0)
        if (-not $ok) { Warn "Some packages from the wheels folder did not install (another Python version?); trying the internet..." }
    }
    if (-not $ok) {
        Say "Installing packages from the internet (first run only)..."
        & $venvPy -m pip install --quiet --timeout 20 -r requirements.txt
        & $venvPy -c $need 2>$null
        $ok = ($LASTEXITCODE -eq 0)
    }
    $ErrorActionPreference = "Stop"
    if ($ok) { New-Item -ItemType File -Force $stamp | Out-Null }   # remembered only when everything is in
}
$ErrorActionPreference = "Continue"
& $venvPy -c "import pymodbus, yaml" 2>$null                # what the measuring session needs at the least
$pkgOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $pkgOk) {
    Warn "The packages could not be installed: no wheels folder for this Python and no route to the package server."
    Warn "Delete the .venv folder, check that the 'wheels' folder is next to SESSION.bat (Python 3.12), start again."
    exit 1
}
if ($InstallOnly) { Say "Packages are in place."; exit 0 }

# ---- 3. Config -------------------------------------------------------------------
$cfg = "config\site.demo.yaml"
if ($Plant -or $Check -or $Trace -or $Probe -or $Session) { $cfg = "config\site.plant.yaml" }
if (-not (Test-Path $cfg)) { throw "config not found: $cfg" }
New-Item -ItemType Directory -Force "data" | Out-Null
Say "Config: $cfg"

# ---- 4a. CHECK: one poll, printed, then exit ----------------------------------------
if ($Check) {
    & $venvPy -m plantcollector.cli --config $cfg --data data check
    exit $LASTEXITCODE
}

# ---- Wireshark (dumpcap/tshark) and Npcap for the SAU watch: found, or installed when missing ---------------------
function Find-Dumpcap {
    $c = @("$env:ProgramFiles\Wireshark\dumpcap.exe", "${env:ProgramFiles(x86)}\Wireshark\dumpcap.exe")
    foreach ($k in "HKLM:\SOFTWARE\Wireshark", "HKLM:\SOFTWARE\WOW6432Node\Wireshark") {
        try { $d = (Get-ItemProperty -Path $k -ErrorAction Stop).InstallDir; if ($d) { $c += (Join-Path $d "dumpcap.exe") } } catch {}
    }
    foreach ($p in $c) { if ($p -and (Test-Path $p)) { return $p } }
    $cmd = Get-Command dumpcap.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}
function Ensure-Wireshark {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not (Find-Dumpcap)) {
        if ($winget) {
            Say "Wireshark is not installed on this PC: installing it now with winget (internet needed, about 80 MB; Windows may ask for permission)..."
            & winget install --id WiresharkFoundation.Wireshark -e --accept-package-agreements --accept-source-agreements
        }
        if (-not (Find-Dumpcap)) {
            Warn "Wireshark could not be installed automatically. Install it from the page that opens now (keep the defaults, including Npcap), then start this again. Until then the session runs without capture."
            Start-Process "https://www.wireshark.org/download.html"
            return
        }
        Say "Wireshark installed."
    }
    if (-not (Test-Path "$env:SystemRoot\System32\Npcap\wpcap.dll")) {
        Warn "Npcap (the part of Wireshark that captures) is missing. Its installer opens now: keep the defaults and press Next."
        if ($winget) { & winget install --id Insecure.Npcap -e --interactive --accept-package-agreements --accept-source-agreements }
        if (-not (Test-Path "$env:SystemRoot\System32\Npcap\wpcap.dll")) { Start-Process "https://npcap.com/#download" }
    }
}

# ---- 4a'. PROBE / TRACE: read-only tools, run in this window ----------------------------------
if ($Probe) {
    & $venvPy -m plantcollector.cli --config $cfg --data data probe
    exit $LASTEXITCODE
}
if ($SessionDemo) {
    $host.UI.RawUI.WindowTitle = "QS Collector - DEMO (simulated plant)"
    Ensure-Wireshark
    Say "DEMO: the SAU watch on a simulated plant on this PC. Every 5 minutes: a busy SAU, a fail on both groups with lost pings, a log download, a fail with the SAU on time, late answers. Nothing is sent to the real master or SAU. Files in data-demo\ and bundles-demo\. Close the window to stop."
    & $venvPy -m plantcollector.cli --config config\site.yaml --data data-demo session-demo
    exit 0
}
if ($Session) {
    Say "SAU watch: runs until this window is closed. The master every second, the master and the SAU pinged every second, the SAU never read. Every comm fail is written here in red as it happens, its Wireshark capture is kept in data\evidence, and a live page opens in the browser. A report and a zip every day at 16:00 Erbil time, for the day before and for today so far."
    $host.UI.RawUI.WindowTitle = "QS Collector - SAU watch (keep this window open)"
    Ensure-Wireshark
    while ($true) {
        & $venvPy -m plantcollector.cli --config $cfg --data data session
        if ($LASTEXITCODE -eq 0) { break }            # stopped on purpose (Ctrl+C)
        Warn "The session stopped unexpectedly (code $LASTEXITCODE); starting it again in 10 seconds. Close this window to stop."
        Start-Sleep -Seconds 10
    }
    exit 0
}
if ($Trace) {
    Say "Trace running in this window: 1 s master, 5 s SAU, files in data\trace. Ctrl+C stops it."
    & $venvPy -m plantcollector.cli --config $cfg --data data trace
    exit $LASTEXITCODE
}

# ---- 4b. FOREGROUND: run in this window (no Windows task) --------------------------------
$runArgs = @("-m", "plantcollector.cli", "--config", $cfg, "--data", "data", "run", "--port", $Port, "--log", "data\collector.log")
if ($Foreground) {
    Say "Running in this window (Ctrl+C to stop). Dashboard: http://localhost:$Port"
    if (-not $NoBrowser) { $runArgs += "--open" }
    & $venvPy @runArgs
    exit $LASTEXITCODE
}

# ---- 4c. BACKGROUND: Windows scheduled task, starts with Windows ------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Say "Asking for administrator rights to register the collector as a Windows task..."
    $flags = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Elevated", "-NoBrowser", "-Port", $Port)
    if ($Plant) { $flags += "-Plant" }
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $flags -Wait
    } catch {
        Warn "Administrator rights refused. Running in this window instead (will not restart with Windows)."
        if (-not $NoBrowser) { $runArgs += "--open" }
        & $venvPy @runArgs
        exit $LASTEXITCODE
    }
} else {
    $action = New-ScheduledTaskAction -Execute $venvPyw -Argument ($runArgs -join " ") -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 100 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*plantcollector.cli*run*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 20; $i++) {       # wait until the previous instance has released the port
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Seconds 1
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Say "Windows task '$TaskName' registered: starts with Windows, restarts if it fails."
    if ($Elevated) { Start-Sleep -Seconds 2 }
}

# ---- 5. Wait for the dashboard, open it --------------------------------------------------
if (-not $Elevated) {
    $url = "http://localhost:$Port"
    $urlFile = Join-Path $root "data\dashboard.url"
    $ok = $false
    if ($Plant) { Write-Host "[collector] Waiting for the collector to answer on $url " -ForegroundColor Cyan -NoNewline }
    else { Write-Host "[collector] Waiting for the collector to answer on $url (the demo first generates 3 days of pretend history) " -ForegroundColor Cyan -NoNewline }
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-Path $urlFile) { $u = (Get-Content $urlFile -ErrorAction SilentlyContinue | Select-Object -First 1); if ($u) { $url = $u.Trim() } }
        try { Invoke-RestMethod "$url/api/v1/health" -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Write-Host "." -NoNewline; Start-Sleep -Seconds 1 }
    }
    Write-Host ""
    if ($ok) {
        Say "Collector is running. Dashboard: $url   (log: data\collector.log)"
        if (-not $NoBrowser) { Start-Process $url }
    } else {
        Warn "The collector did not answer on $url within 90 s. Diagnostics:"
        try {
            $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
            $info = $t | Get-ScheduledTaskInfo
            Write-Host ("  task state: {0}   last run: {1}   last result: {2}" -f $t.State, $info.LastRunTime, $info.LastTaskResult)
        } catch { Write-Host "  task '$TaskName' not found" }
        $log = Join-Path $root "data\collector.log"
        if (Test-Path $log) { Write-Host "  last lines of data\collector.log:"; Get-Content $log -Tail 15 | ForEach-Object { Write-Host "    $_" } }
        else { Write-Host "  data\collector.log was not created - the task did not start Python at all." }
        Write-Host "  To see the error directly, run:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Foreground" -ForegroundColor Yellow
    }
}
