# Run by the Setup (installer\CCI-Plant-Monitoring.nsi), as administrator. Two phases:
#
#   -Phase Stop      before the files are copied: stops the background service and every program process
#   -Phase Install   after the files are copied (the program brings its own Python: nothing is downloaded or installed
#                    besides what is listed here):
#       1. settings and data in C:\ProgramData\CCI Plant Monitoring (never in the program folder, never overwritten:
#          an update keeps them; the first install can bring them over from an earlier copy)
#       2. the background service: Windows task "PlantDataCollector" runs "CCI Plant Monitoring Service.exe"
#          (at startup, as SYSTEM, restarted every minute if it stops)
#       3. the updater: Windows task "PlantCollectorUpdater" (scripts\updater.ps1, GitHub Releases)
#       4. firewall rules for Turkey's Modbus port and the dashboard (optional)
#       5. the "CCI Plant Monitoring" shortcut (Start Menu, Desktop) to "CCI Plant Monitoring.exe": the window
#       6. starts the service and waits until it answers
#
# Everything is safe to run again (the Setup does it on every update). Output goes to the Setup's window and to
# C:\ProgramData\CCI Plant Monitoring\install.log.
param(
    [ValidateSet("Stop", "Install")] [string]$Phase = "Install",
    [string]$InstallDir = "",
    [string]$AutoUpdate = "1",
    [string]$Firewall = "1",
    [string]$Desktop = "1",
    [string]$Import = "",
    [string]$SourceAddress = "",
    [string]$Version = "",
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
if (-not $InstallDir) { $InstallDir = Split-Path -Parent $PSScriptRoot }
$InstallDir = $InstallDir.TrimEnd("\")
$TaskName = "PlantDataCollector"
$UpdaterTask = "PlantCollectorUpdater"
$FwGroup = "CCI Plant Monitoring"
$ServiceExe = Join-Path $InstallDir "CCI Plant Monitoring Service.exe"
$WindowExe = Join-Path $InstallDir "CCI Plant Monitoring.exe"
$Pd = Join-Path $env:ProgramData "CCI Plant Monitoring"
$CfgDir = Join-Path $Pd "config"
$DataDir = Join-Path $Pd "data"
$UpdDir = Join-Path $Pd "update"
foreach ($d in $Pd, $CfgDir, $DataDir, $UpdDir) { New-Item -ItemType Directory -Force $d | Out-Null }
$LogFile = Join-Path $Pd "install.log"

function Say($m) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Output $m
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Stop-Collector {
    # the service task, then any program process left over: this copy's exes, and the collector or window of an
    # earlier copy (python with plantcollector.cli / app\desktop.py on its command line)
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop; Say "background service stopped" } catch {}
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -eq "CCI Plant Monitoring Service.exe") -or ($_.Name -eq "CCI Plant Monitoring.exe") -or
        ($_.CommandLine -and ($_.CommandLine -like "*plantcollector.cli*" -or $_.CommandLine -like "*app\desktop.py*")) })
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Say ("stopped process {0} ({1})" -f $p.ProcessId, $p.Name) } catch {}
    }
    for ($i = 0; $i -lt 20; $i++) {                       # until the ports are free again
        $busy = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq $Port -or $_.LocalPort -eq 502 }
        if (-not $busy) { break }
        Start-Sleep -Seconds 1
    }
}

# ======================================================================== Phase Stop
if ($Phase -eq "Stop") {
    Say "=== CCI Plant Monitoring: stopping before the files are copied ==="
    # remember where an earlier copy ran from, before its task is replaced (the Install phase may bring its data over)
    try {
        $old = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $wd = "$($old.Actions[0].WorkingDirectory)".Trim('"').TrimEnd("\")
        if ($wd -and ($wd -ne $InstallDir)) { Set-Content -Path (Join-Path $Pd "previous-copy.txt") -Value $wd -Encoding UTF8; Say "an earlier copy ran from $wd" }
    } catch {}
    Stop-Collector
    exit 0
}

# ======================================================================== Phase Install
Say "=== CCI Plant Monitoring $($Version): setting up in $InstallDir ==="
Set-Location $InstallDir
if (-not (Test-Path $ServiceExe) -or -not (Test-Path (Join-Path $InstallDir "runtime\python313.dll"))) {
    Say "ERROR: the program files are incomplete in $InstallDir (no service exe or runtime). Run the Setup again."
    exit 10
}

# ---- 1. settings and data in ProgramData ---------------------------------------------------------------------
$cfgName = "site.plant.yaml"
$cfg = Join-Path $CfgDir $cfgName
$prevFile = Join-Path $Pd "previous-copy.txt"
if (-not $Import -and (Test-Path $prevFile)) {           # an earlier copy that ran as the Windows task, if any
    $prev = (Get-Content $prevFile -TotalCount 1).Trim()
    if ($prev -and (Test-Path (Join-Path $prev "config\$cfgName"))) { $Import = $prev; Say "found the earlier copy in $($prev): its settings and data are brought over when this PC has none yet" }
}
Remove-Item $prevFile -Force -ErrorAction SilentlyContinue
if ($Import) { $Import = $Import.Trim('"').TrimEnd("\") }
if (-not (Test-Path $cfg)) {
    $from = Join-Path $InstallDir "config\$cfgName"
    if ($Import -and (Test-Path (Join-Path $Import "config\$cfgName"))) { $from = Join-Path $Import "config\$cfgName"; Say "settings taken from the earlier copy: $from" }
    Copy-Item $from $cfg
    Say "settings: $cfg (new)"
} else {
    Say "settings: $cfg (kept as they are)"
}
if ($Import -and -not (Test-Path (Join-Path $DataDir "telemetry.sqlite"))) {
    $oldData = Join-Path $Import "data"
    foreach ($f in "telemetry.sqlite", "telemetry.sqlite-wal", "telemetry.sqlite-shm", "settings.json") {
        $src = Join-Path $oldData $f
        if (Test-Path $src) { Copy-Item $src (Join-Path $DataDir $f); Say "data brought over: $f" }
    }
}

# the PC's own plant address, when another network adapter of the PC is in the same range (a Wi-Fi or 4G router)
$cfgText = Get-Content $cfg -Raw
if ($cfgText -notmatch "(?m)^network:") {
    $addr = $SourceAddress.Trim()
    $ipObj = $null
    if ($addr -and -not ([Net.IPAddress]::TryParse($addr, [ref]$ipObj) -and $addr -match "^\d+\.\d+\.\d+\.\d+$")) {
        Say "WARNING: '$addr' is not an IPv4 address; this PC's plant address is chosen automatically instead"
        $addr = ""
    }
    if (-not $addr) {
        $m = [regex]::Match($cfgText, "host:\s*(\d+\.\d+\.\d+)\.\d+")
        if ($m.Success) {
            $net = $m.Groups[1].Value + "."
            $mine = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress.StartsWith($net) })
            $ifs = @($mine | Select-Object -ExpandProperty InterfaceIndex -Unique)
            if ($ifs.Count -gt 1) {
                foreach ($a in $mine) {
                    $gw = (Get-NetIPConfiguration -InterfaceIndex $a.InterfaceIndex -ErrorAction SilentlyContinue).IPv4DefaultGateway
                    if (-not $gw) { $addr = $a.IPAddress; break }
                }
                if ($addr) { Say "this PC has two adapters in the plant's range $($net)x: the plant traffic goes out from $addr (the one without a gateway)" }
                else { Say "WARNING: two adapters of this PC are in $($net)x and both have a gateway; set network.source_address in $cfg by hand" }
            }
        }
    }
    if ($addr) {
        Add-Content -Path $cfg -Value "`r`n# Added by the Setup: this PC's address on the plant cable (another adapter is in the same range).`r`nnetwork:`r`n  source_address: $addr`r`n" -Encoding UTF8
        Say "network.source_address: $addr written to the settings"
    }
}

# the updater's settings (kept on an update; the Setup's choice wins only for 'auto')
$us = Join-Path $UpdDir "settings.json"
$u = [ordered]@{ auto = ($AutoUpdate -eq "1"); window_start_hour = 1; window_end_hour = 5; repo = "Tishkos/applicationCocaCola"; skip = "" }
if (Test-Path $us) {
    try {
        $j = Get-Content $us -Raw | ConvertFrom-Json
        foreach ($k in "window_start_hour", "window_end_hour", "repo", "skip") { if ($null -ne $j.$k) { $u[$k] = $j.$k } }
    } catch {}
}
if ($Version -and "$($u.skip)" -eq $Version) { $u.skip = "" }   # this version is being installed now: no longer skipped
($u | ConvertTo-Json) | Set-Content -Path $us -Encoding UTF8
Say ("updates: " + $(if ($u.auto) { "installed automatically between {0:00}:00 and {1:00}:00" -f [int]$u.window_start_hour, [int]$u.window_end_hour } else { "checked, installed only when asked" }))

# ---- 2. the background service --------------------------------------------------------------------------------
Stop-Collector
$log = Join-Path $DataDir "collector.log"
Remove-Item (Join-Path $DataDir "dashboard.url") -Force -ErrorAction SilentlyContinue
$argLine = "--config `"$cfg`" --data `"$DataDir`" run --port $Port --log `"$log`""
$action = New-ScheduledTaskAction -Execute "`"$ServiceExe`"" -Argument $argLine -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 100 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "CCI Plant Monitoring: the read-only collector, the dashboard and the Turkey feed (installed by the Setup)" -Force | Out-Null
Say "background service '$TaskName' registered: starts with Windows, restarts if it stops"
# Users without administrator rights may START it (the window does that when Windows had stopped it); only
# administrators can change or delete it. Read + execute for "Authenticated Users" on the task.
try {
    $ts = New-Object -ComObject Schedule.Service
    $ts.Connect()
    $t = $ts.GetFolder("\").GetTask($TaskName)
    $sd = $t.GetSecurityDescriptor(4)                     # DACL only
    if ($sd -notmatch "\(A;;GRGX;;;AU\)") { $t.SetSecurityDescriptor($sd + "(A;;GRGX;;;AU)", 0) }
    Say "users may start the background service (not change it)"
} catch { Say "note: users without administrator rights cannot start the service by hand ($($_.Exception.Message))" }

# ---- 3. the updater -------------------------------------------------------------------------------------------
$uAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InstallDir\scripts\updater.ps1`"" -WorkingDirectory $InstallDir
$t1 = New-ScheduledTaskTrigger -Daily -At "01:30"
$t1.Repetition = (New-ScheduledTaskTrigger -Once -At "01:30" -RepetitionInterval (New-TimeSpan -Hours 4) -RepetitionDuration (New-TimeSpan -Hours 23)).Repetition
$t2 = New-ScheduledTaskTrigger -AtStartup
$t2.Delay = "PT10M"
$uSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$running = $false
try { $running = ((Get-ScheduledTask -TaskName $UpdaterTask -ErrorAction Stop).State -eq "Running") } catch {}
if ($running) {
    Say "updater '$UpdaterTask' is running this update: its task is left as it is"
} else {
    Register-ScheduledTask -TaskName $UpdaterTask -Action $uAction -Trigger @($t1, $t2) -Settings $uSettings -Principal $principal `
        -Description "CCI Plant Monitoring: checks GitHub Releases for a newer version (installed by the Setup)" -Force | Out-Null
    Say "updater '$UpdaterTask' registered: checks every 4 hours and 10 minutes after start-up"
}

# ---- 4. firewall ------------------------------------------------------------------------------------------------
Get-NetFirewallRule -Group $FwGroup -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
if ($Firewall -eq "1") {
    New-NetFirewallRule -DisplayName "CCI Plant Monitoring - Turkey Modbus (TCP 502)" -Group $FwGroup -Direction Inbound -Protocol TCP `
        -LocalPort 502 -Action Allow -Profile Any | Out-Null
    New-NetFirewallRule -DisplayName "CCI Plant Monitoring - dashboard (TCP $Port)" -Group $FwGroup -Direction Inbound -Protocol TCP `
        -LocalPort $Port -Action Allow -Profile Any | Out-Null
    Say "firewall: TCP 502 (Turkey's Modbus) and $Port (dashboard) open for incoming connections"
} else {
    Say "firewall: not changed"
}

# ---- 5. the application window: shortcuts -----------------------------------------------------------------------
$sc = Join-Path $InstallDir "app\Install-DesktopShortcut.ps1"
try {
    & $sc -Remove -Name "Plant Collector" | Out-Null      # the shortcuts of an earlier copy: they would start that copy again
    & $sc -Remove -Name "Plant Collector" -AllUsers | Out-Null
    & $sc -Remove -Name "CCI Plant Monitoring" -AllUsers | Out-Null
    $p = @{ AllUsers = $true; Name = "CCI Plant Monitoring"; Target = $WindowExe; Arguments = "" }
    if ($Desktop -ne "1") { $p.NoDesktop = $true }
    & $sc @p | ForEach-Object { Say $_ }
} catch { Say "WARNING: shortcut not written: $($_.Exception.Message)" }

# ---- 6. start and wait -------------------------------------------------------------------------------------------
Start-ScheduledTask -TaskName $TaskName
Say "starting the background service..."
$url = "http://127.0.0.1:$Port/"
$up = $false
for ($i = 0; $i -lt 120; $i++) {
    $f = Join-Path $DataDir "dashboard.url"
    if (Test-Path $f) { $t = (Get-Content $f -TotalCount 1 -ErrorAction SilentlyContinue); if ($t) { $url = $t.Trim().Replace("localhost", "127.0.0.1"); if (-not $url.EndsWith("/")) { $url += "/" } } }
    try { Invoke-RestMethod ($url + "api/v1/health") -TimeoutSec 2 -UseBasicParsing | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 1 }
}
if ($up) {
    Say "the background service answers: $url"
    if (-not $running) {                                  # let Settings > Updates show the newest release at once
        try { Set-Content -Path (Join-Path $UpdDir "now.flag") -Value "check" -Encoding ASCII; Start-ScheduledTask -TaskName $UpdaterTask } catch {}
    }
    exit 0
}
Say "WARNING: the background service did not answer within 2 minutes. Last lines of $log :"
if (Test-Path $log) { Get-Content $log -Tail 15 | ForEach-Object { Say "  $_" } }
exit 20
