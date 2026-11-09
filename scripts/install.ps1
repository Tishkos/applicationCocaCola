# Run by the Setup (installer\CCI-Plant-Monitoring.nsi), as administrator. Two phases:
#
#   -Phase Stop      before the files are copied: marks "updating" (the exes then refuse to start), disables and
#                    stops the background service and every program process
#   -Phase Install   after the files are copied (the program brings its own Python: nothing is downloaded or installed
#                    besides what is listed here):
#       1. settings and data in C:\ProgramData\CCI Plant Monitoring (never in the program folder, never overwritten:
#          an update keeps them; the first install can bring them over from an earlier copy)
#       2. the background service: Windows task "PlantDataCollector" runs "CCI Plant Monitoring Service.exe"
#          (at startup and every 5 minutes if it is not running, as SYSTEM)
#       3. the updater: Windows task "PlantCollectorUpdater" (scripts\updater.ps1, GitHub Releases)
#       4. firewall rules for Turkey's Modbus port and the dashboard (optional)
#       5. the "CCI Plant Monitoring" shortcut (Start Menu, Desktop) to "CCI Plant Monitoring.exe": the window,
#          and the same shortcut in the all-users Startup folder when the window is to open at sign-in (optional)
#       6. starts the service and waits until it answers
#   The Setup's "Turkey Modbus feed" box switches publish.modbus_tcp.enabled in the settings (step 1, through the
#   program's own `turkey on|off` command, which changes that one value and nothing else in the file).
#
# Everything is safe to run again (the Setup does it on every update). Output goes to the Setup's window and to
# C:\ProgramData\CCI Plant Monitoring\install.log.
# C:\ProgramData\CCI Plant Monitoring is made readable, not writable, for users (SYSTEM and administrators only may
# change it): the service and the updater run as SYSTEM from what is in there.
param(
    [ValidateSet("Stop", "Install")] [string]$Phase = "Install",
    [string]$InstallDir = "",
    [string]$AutoUpdate = "1",
    [string]$Firewall = "1",
    [string]$Desktop = "1",
    [string]$Import = "",
    [string]$SourceAddress = "",
    [string]$Turkey = "",        # "1" switches Turkey's Modbus feed on in the settings, "0" off, "" leaves it as it is
    [string]$StartWindow = "",   # "1" opens the window when a user signs in, "0" not, "" leaves it as it is
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
$Marker = Join-Path $UpdDir "installing.flag"             # while it exists (and is recent) the two exes do not start
$LogFile = Join-Path $Pd "install.log"
$Link = [IO.FileAttributes]::ReparsePoint

function Remove-Links($dir) {
    # junctions and symbolic links inside $dir are deleted as links, never followed
    foreach ($i in @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue)) {
        if ($i.Attributes -band $Link) {
            try { if ($i.PSIsContainer) { [IO.Directory]::Delete($i.FullName, $false) } else { [IO.File]::Delete($i.FullName) } } catch {}
        } elseif ($i.PSIsContainer) { Remove-Links $i.FullName }
    }
}

function Protect-Data {
    # owner Administrators; full control for SYSTEM and Administrators; read for Users; nothing else (an earlier
    # permission, or a folder someone created before the Setup, is replaced)
    $ErrorActionPreference = "Continue"
    $item = Get-Item -LiteralPath $Pd -Force -ErrorAction SilentlyContinue
    if ($item -and ($item.Attributes -band $Link)) { [IO.Directory]::Delete($Pd, $false) }
    New-Item -ItemType Directory -Force $Pd | Out-Null
    & icacls.exe $Pd /setowner "*S-1-5-32-544" /C /Q | Out-Null
    & icacls.exe $Pd /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" /C /Q | Out-Null
    Remove-Links $Pd
    if (@(Get-ChildItem -LiteralPath $Pd -Force -ErrorAction SilentlyContinue).Count) {
        & icacls.exe "$Pd\*" /setowner "*S-1-5-32-544" /T /C /Q | Out-Null
        & icacls.exe "$Pd\*" /reset /T /C /Q | Out-Null
    }
    foreach ($d in $CfgDir, $DataDir, $UpdDir) { New-Item -ItemType Directory -Force $d | Out-Null }
}
Protect-Data

function Say($m) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Output $m
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Stop-Collector {
    # the service task, then any program process left over: this copy's exes, and the collector or window of an
    # earlier copy (python with plantcollector.cli / app\desktop.py on its command line)
    try { Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null } catch {}   # no restart while files change
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

function Set-Installed {
    # Settings > Updates shows this version as installed at once, whoever ran the Setup (the updater, or a person)
    $sf = Join-Path $UpdDir "status.json"
    try {
        $o = $null
        if (Test-Path $sf) { $o = Get-Content $sf -Raw | ConvertFrom-Json }
        if ($null -eq $o) { $o = New-Object PSObject }
        $at = Get-Date -Format "yyyy-MM-dd HH:mm"
        $newer = $false
        try { $newer = $o.latest -and ([version]"$($o.latest)" -gt [version]$Version) } catch {}
        foreach ($kv in @(@("state", "up to date"), @("message", "Version $Version installed $at."), @("last_install", "$Version at $at"),
                          @("installed", $Version), @("last_check", $at))) {
            $o | Add-Member -NotePropertyName $kv[0] -NotePropertyValue $kv[1] -Force
        }
        if (-not $newer) {
            $o | Add-Member -NotePropertyName latest -NotePropertyValue $Version -Force
            $o | Add-Member -NotePropertyName available -NotePropertyValue $null -Force
        }
        [IO.File]::WriteAllText($sf, ($o | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
    } catch {}
}

function Done($code) {
    # every way out of the Install phase: the service task enabled again, the "updating" mark removed
    try { Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null } catch {}
    Remove-Item $Marker -Force -ErrorAction SilentlyContinue
    if ($Phase -eq "Install" -and ($code -eq 0 -or $code -eq 20) -and $Version) { Set-Installed }
    exit $code
}
trap {
    Say "ERROR: $($_.Exception.Message) (line $($_.InvocationInfo.ScriptLineNumber))"
    if ($Phase -eq "Install") { Done 1 } else { exit 1 }
}

# ======================================================================== Phase Stop
if ($Phase -eq "Stop") {
    Say "=== CCI Plant Monitoring: stopping before the files are copied ==="
    Set-Content -Path $Marker -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss") -Encoding ASCII
    # remember where an earlier copy ran from, before its task is replaced (the Install phase may bring its data over)
    try {
        $old = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $wd = "$($old.Actions[0].WorkingDirectory)".Trim('"').TrimEnd("\")
        if ($wd -and ($wd -ne $InstallDir)) { Set-Content -Path (Join-Path $Pd "previous-copy.txt") -Value $wd -Encoding UTF8; Say "an earlier copy ran from $wd" }
    } catch {}
    Stop-Collector
    # an earlier copy in this very folder: its settings and data are taken now, before the Setup replaces its config\
    $oldCfg = Join-Path $InstallDir "config\site.plant.yaml"
    $newCfg = Join-Path $CfgDir "site.plant.yaml"
    if ((Test-Path (Join-Path $InstallDir "scripts\run.ps1")) -and (Test-Path $oldCfg) -and -not (Test-Path $newCfg)) {
        Copy-Item $oldCfg $newCfg
        Say "settings taken from the earlier copy in this folder: $oldCfg"
        if (-not (Test-Path (Join-Path $DataDir "telemetry.sqlite"))) {
            foreach ($f in "telemetry.sqlite", "telemetry.sqlite-wal", "telemetry.sqlite-shm", "settings.json") {
                $src = Join-Path $InstallDir "data\$f"
                if (Test-Path $src) { Copy-Item $src (Join-Path $DataDir $f); Say "data brought over: $f" }
            }
            $oa = Join-Path $InstallDir "data\archive"
            if ((Test-Path $oa) -and -not (Test-Path (Join-Path $DataDir "archive"))) { Copy-Item $oa (Join-Path $DataDir "archive") -Recurse; Say "data brought over: archive (one file per month)" }
        }
    }
    exit 0
}

# ======================================================================== Phase Install
Say "=== CCI Plant Monitoring $($Version): setting up in $InstallDir ==="
Set-Location $InstallDir
if (-not (Test-Path $ServiceExe) -or -not (Test-Path (Join-Path $InstallDir "runtime\python313.dll"))) {
    Say "ERROR: the program files are incomplete in $InstallDir (no service exe or runtime). Run the Setup again."
    Done 10
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
    $oa = Join-Path $oldData "archive"
    if ((Test-Path $oa) -and -not (Test-Path (Join-Path $DataDir "archive"))) { Copy-Item $oa (Join-Path $DataDir "archive") -Recurse; Say "data brought over: archive (one file per month)" }
}

# the master's live reading: every minute, unless a rate was already chosen in Settings > Reading the devices (a config
# from an earlier copy reads it every 2 s). Written once, without a BOM (the collector reads this file).
$sj = Join-Path $DataDir "settings.json"
try {
    $o = $null
    if (Test-Path $sj) { $o = Get-Content $sj -Raw | ConvertFrom-Json }
    if ($null -eq $o) { $o = New-Object PSObject }
    if ($null -eq $o.live_every_s) {
        $o | Add-Member -NotePropertyName live_every_s -NotePropertyValue 60 -Force
        [IO.File]::WriteAllText($sj, ($o | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding $false))
        Say "the master is read every minute for the live view (Settings > Reading the devices changes it)"
    }
} catch { Say "note: settings.json not changed ($($_.Exception.Message))" }

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

# Turkey's Modbus feed (TCP 502): the Setup's box switches publish.modbus_tcp.enabled in the settings file through the
# program's own command, which changes that one value and keeps every other line. Not touched when no choice was made
# (an update by the updater, or an old Setup's choices without this box).
if ($Turkey -eq "1" -or $Turkey -eq "0") {
    $word = $(if ($Turkey -eq "1") { "on" } else { "off" })
    $py = Join-Path $InstallDir "runtime\python.exe"
    $rc = -1
    try {
        $ErrorActionPreference = "Continue"
        $env:PYTHONHOME = Join-Path $InstallDir "runtime"
        $env:PYTHONPATH = $InstallDir
        $out = & $py -s -m plantcollector.cli --config "$cfg" --data "$DataDir" turkey $word 2>&1
        $rc = $LASTEXITCODE
        foreach ($l in @($out)) { if ("$l".Trim()) { Say "$l" } }
    } catch { Say "note: $($_.Exception.Message)" }
    finally {
        $ErrorActionPreference = "Stop"
        Remove-Item Env:PYTHONHOME, Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    if ($rc -ne 0) { Say "WARNING: Turkey's Modbus feed could not be switched $word (code $rc): set publish.modbus_tcp.enabled in $cfg by hand" }
    elseif ($Turkey -eq "1") { Say "Turkey's Modbus feed: on, TCP 502 on every address of this PC (the firewall box opens the port)" }
    else { Say "Turkey's Modbus feed: off" }
} else {
    Say "Turkey's Modbus feed: as in the settings (not changed)"
}

# the updater's settings (kept on an update; the Setup's choice wins only for 'auto')
$us = Join-Path $UpdDir "settings.json"
$u = [ordered]@{ auto = ($AutoUpdate -eq "1"); window_start_hour = 1; window_end_hour = 5; skip = "" }
if (Test-Path $us) {
    try {
        $j = Get-Content $us -Raw | ConvertFrom-Json
        foreach ($k in "window_start_hour", "window_end_hour", "skip") { if ($null -ne $j.$k) { $u[$k] = $j.$k } }
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
# at start-up, and every 5 minutes: a collector that ended (or crashed) is started again within 5 minutes; while it
# runs, the 5-minute trigger does nothing (IgnoreNew)
$trigger = @((New-ScheduledTaskTrigger -AtStartup),
             (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)))
$settings = New-ScheduledTaskSettingsSet -RestartCount 100 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "CCI Plant Monitoring: the read-only collector, the dashboard and the Turkey feed (installed by the Setup)" -Force | Out-Null
Say "background service '$TaskName' registered: starts with Windows, started again within 5 minutes if it stops"
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
    Say "firewall: no rules from this Setup (the box was not ticked); Turkey's Modbus needs TCP 502 open"
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

# the window at sign-in: the Start Menu shortcut copied into the all-users Startup folder (the service runs anyway;
# this only opens the window, so nobody has to open a browser on "localhost"). Left as it is when no choice was made.
try {
    $startupLnk = Join-Path ([Environment]::GetFolderPath("CommonStartup")) "CCI Plant Monitoring.lnk"
    $menuLnk = Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "CCI Plant Monitoring.lnk"
    if ($StartWindow -eq "1") {
        if (Test-Path $menuLnk) { Copy-Item $menuLnk $startupLnk -Force; Say "window: opens when a user signs in to Windows ($startupLnk)" }
        else { Say "WARNING: no Start Menu shortcut to copy into the Startup folder; the window does not open at sign-in" }
    } elseif ($StartWindow -eq "0") {
        if (Test-Path $startupLnk) { Remove-Item $startupLnk -Force }
        Say "window: not opened at sign-in (Start Menu and Desktop only)"
    } elseif (Test-Path $startupLnk) {
        Copy-Item $menuLnk $startupLnk -Force                # kept, renewed to this version's shortcut
        Say "window: still opens when a user signs in"
    }
} catch { Say "WARNING: Startup shortcut not written: $($_.Exception.Message)" }

$wv = $false
foreach ($k in "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
               "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}") {
    try { $pv = (Get-ItemProperty -Path $k -Name pv -ErrorAction Stop).pv; if ($pv -and [int]($pv.Split(".")[0]) -ge 86) { $wv = $true } } catch {}
}
if ($wv) { Say "window: Microsoft Edge WebView2 Runtime found" }
else { Say "NOTE: no Microsoft Edge WebView2 Runtime found for all users. The service and Turkey's feed do not need it; the window does (it says so and offers a browser instead). Install it from Microsoft: 'WebView2 Runtime' (Evergreen)." }

# ---- 6. start and wait -------------------------------------------------------------------------------------------
Remove-Item $Marker -Force -ErrorAction SilentlyContinue
Enable-ScheduledTask -TaskName $TaskName | Out-Null
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
    if ($Version) { Set-Installed }                       # before the updater reads the status file
    if (-not $running) {                                  # let Settings > Updates show the newest release at once
        try { Set-Content -Path (Join-Path $UpdDir "now.flag") -Value "check" -Encoding ASCII; Start-ScheduledTask -TaskName $UpdaterTask } catch {}
    }
    Done 0
}
Say "WARNING: the background service did not answer within 2 minutes. Last lines of $log :"
if (Test-Path $log) { Get-Content $log -Tail 15 | ForEach-Object { Say "  $_" } }
Done 20
