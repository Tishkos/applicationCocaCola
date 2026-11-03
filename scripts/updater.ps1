# The auto-updater: Windows task "PlantCollectorUpdater" (as SYSTEM; the Setup registers it). It runs every 4 hours,
# 10 minutes after Windows starts, and at once when "Check for updates" / "Install now" is clicked in the dashboard
# (Settings > About: the collector drops now.flag in the update folder and starts this task).
#
#   1. asks GitHub for the newest published release of the program (Releases of the repository in update\settings.json)
#   2. newer than the installed version (version.txt)?
#        - installs it when "Install now" was clicked, or automatically at night (01:00-05:00, the PC's own clock)
#          when automatic updates are on; otherwise only reports "available" in the dashboard
#   3. installs: downloads the release's CCI-Plant-Monitoring-Setup-<version>.exe and SHA256SUMS.txt, checks the
#      SHA-256, runs the Setup quietly (/S /UPDATE: the settings and the data stay as they are)
#   4. checks the background service answers afterwards; if not, puts the previous version back (its Setup is kept)
#      and does not try that release again
#
# Writes C:\ProgramData\CCI Plant Monitoring\update\status.json (read by the dashboard) and updater.log.
# Only reads from GitHub; never sends anything about the plant or the PC.
param([switch]$Now, [switch]$Install)
$ErrorActionPreference = "Stop"
$InstallDir = Split-Path -Parent $PSScriptRoot
$Pd = Join-Path $env:ProgramData "CCI Plant Monitoring"
$UpdDir = Join-Path $Pd "update"
New-Item -ItemType Directory -Force $UpdDir | Out-Null
$LogFile = Join-Path $UpdDir "updater.log"
$StatusFile = Join-Path $UpdDir "status.json"
$Flag = Join-Path $UpdDir "now.flag"
$SetupNow = Join-Path $UpdDir "installed-setup.exe"       # the Setup of the installed version (the Setup copies itself here)
$SetupPrev = Join-Path $UpdDir "previous-setup.exe"       # the one before: the way back if an update does not start
$Port = 8080

function Say($m) {
    try {
        if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) { Move-Item $LogFile "$LogFile.old" -Force }
        Add-Content -Path $LogFile -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8
    } catch {}
    Write-Output $m
}

$cfg = @{ auto = $true; window_start_hour = 1; window_end_hour = 5; repo = "Tishkos/applicationCocaCola"; skip = "" }
try {
    $j = Get-Content (Join-Path $UpdDir "settings.json") -Raw | ConvertFrom-Json
    foreach ($k in @($cfg.Keys)) { if ($null -ne $j.$k) { $cfg[$k] = $j.$k } }
} catch {}
$old = @{}
try { $o = Get-Content $StatusFile -Raw | ConvertFrom-Json; foreach ($p in $o.PSObject.Properties) { $old[$p.Name] = $p.Value } } catch {}
$window = "{0:00}:00-{1:00}:00" -f [int]$cfg.window_start_hour, [int]$cfg.window_end_hour

function Save-Status($state, $message, $extra = @{}) {
    $s = [ordered]@{
        state = $state; message = $message; latest = $old.latest; available = $old.available
        last_check = (Get-Date).ToString("yyyy-MM-dd HH:mm"); last_install = $old.last_install
        auto = [bool]$cfg.auto; window = $window; installed = (Get-Installed)
    }
    foreach ($k in $extra.Keys) { $s[$k] = $extra[$k] }
    foreach ($k in "latest", "available", "last_install") { $old[$k] = $s[$k] }
    $tmp = "$StatusFile.tmp"
    ($s | ConvertTo-Json) | Set-Content -Path $tmp -Encoding UTF8
    Move-Item $tmp $StatusFile -Force
}

function Get-Installed {
    try { return (Get-Content (Join-Path $InstallDir "version.txt") -TotalCount 1).Trim() } catch { return "0.0.0" }
}

function Get-Ver($text) {
    $t = ("$text").Trim().TrimStart("v", "V")
    $m = [regex]::Match($t, "^\d+(\.\d+){1,3}")
    if (-not $m.Success) { return $null }
    $v = $m.Value
    if ($v -notmatch "\.") { $v += ".0" }
    return [version]$v
}

function Test-Up {
    $url = "http://127.0.0.1:$Port/"
    try {
        $f = Join-Path $Pd "data\dashboard.url"
        if (Test-Path $f) { $t = (Get-Content $f -TotalCount 1).Trim().Replace("localhost", "127.0.0.1"); if ($t) { $url = $t.TrimEnd("/") + "/" } }
    } catch {}
    try { Invoke-RestMethod ($url + "api/v1/health") -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

function Wait-Up($seconds) {
    for ($i = 0; $i -lt $seconds; $i += 3) { if (Test-Up) { return $true }; Start-Sleep -Seconds 3 }
    return $false
}

# ---- what was asked ---------------------------------------------------------------------------------------------
$mode = "timer"
if ($Install) { $mode = "install" } elseif ($Now) { $mode = "check" }
if (Test-Path $Flag) {
    $f = (Get-Content $Flag -TotalCount 1 -ErrorAction SilentlyContinue)
    $mode = if ("$f" -like "install*") { "install" } else { "check" }
    Remove-Item $Flag -Force -ErrorAction SilentlyContinue
}
$installed = Get-Installed
Say "--- check ($mode), installed $installed, repository $($cfg.repo)"

# ---- 1. the newest release on GitHub ----------------------------------------------------------------------------
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"
$headers = @{ "User-Agent" = "CCI-Plant-Monitoring-Updater/$installed"; "Accept" = "application/vnd.github+json" }
try {
    $rel = Invoke-RestMethod "https://api.github.com/repos/$($cfg.repo)/releases/latest" -Headers $headers -TimeoutSec 30 -UseBasicParsing
} catch {
    $code = $null
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    if ($code -eq 404) {
        Save-Status "up to date" "No release is published on GitHub yet; $installed is installed." @{ latest = $null; available = $null }
        Say "no release published yet"
        exit 0
    }
    Save-Status "error" ("Could not reach GitHub: " + $_.Exception.Message + " (the next check is in 4 hours).")
    Say "GitHub not reachable: $($_.Exception.Message)"
    exit 1
}
$tag = "$($rel.tag_name)"
$latest = Get-Ver $tag
$have = Get-Ver $installed
if (-not $latest) { Save-Status "error" "The newest release on GitHub ($tag) has no version number like v1.2.3."; Say "bad tag $tag"; exit 1 }
$latestText = $latest.ToString()
if (-not $have -or $latest -le $have) {
    Save-Status "up to date" "$installed is the newest version." @{ latest = $latestText; available = $null }
    Say "up to date (newest on GitHub: $tag)"
    exit 0
}
if ("$($cfg.skip)" -eq $latestText -and $mode -ne "install") {
    Save-Status "update available" "$latestText did not start on this PC last time and was taken back; it is installed only with 'Install now'." @{ latest = $latestText; available = $latestText }
    Say "$latestText is on the skip list (it failed before)"
    exit 0
}

# ---- 2. install now? ---------------------------------------------------------------------------------------------
$hour = (Get-Date).Hour
$inWindow = ($hour -ge [int]$cfg.window_start_hour -and $hour -lt [int]$cfg.window_end_hour)
$go = ($mode -eq "install") -or ([bool]$cfg.auto -and $inWindow)
if (-not $go) {
    $when = if ([bool]$cfg.auto) { "It is installed automatically tonight between $window, or now with 'Install now'." } else { "Automatic updates are off: use 'Install now'." }
    Save-Status "update available" "Version $latestText is available (installed: $installed). $when" @{ latest = $latestText; available = $latestText }
    Say "available: $latestText (not installing now)"
    exit 0
}

# ---- 3. download and check ---------------------------------------------------------------------------------------
$asset = @($rel.assets | Where-Object { $_.name -like "CCI-Plant-Monitoring-Setup-*.exe" }) | Select-Object -First 1
$sums = @($rel.assets | Where-Object { $_.name -eq "SHA256SUMS.txt" }) | Select-Object -First 1
if (-not $asset -or -not $sums) {
    Save-Status "error" "Release $tag has no Setup file and SHA256SUMS.txt; nothing installed." @{ latest = $latestText; available = $latestText }
    Say "release $tag lacks the Setup or SHA256SUMS.txt"
    exit 1
}
$dl = Join-Path $UpdDir "download"
if (Test-Path $dl) { Remove-Item -Recurse -Force $dl }
New-Item -ItemType Directory -Force $dl | Out-Null
$exe = Join-Path $dl $asset.name
Save-Status "downloading" "Downloading version $latestText..." @{ latest = $latestText; available = $latestText }
Say "downloading $($asset.name) ($([math]::Round($asset.size / 1MB, 1)) MB)"
try {
    Invoke-WebRequest $asset.browser_download_url -OutFile $exe -Headers @{ "User-Agent" = $headers["User-Agent"] } -TimeoutSec 600 -UseBasicParsing
    $sumText = (Invoke-WebRequest $sums.browser_download_url -Headers @{ "User-Agent" = $headers["User-Agent"] } -TimeoutSec 60 -UseBasicParsing).Content
    if ($sumText -is [byte[]]) { $sumText = [Text.Encoding]::UTF8.GetString($sumText) }
} catch {
    Save-Status "error" ("Download failed: " + $_.Exception.Message + "; nothing installed.") @{ latest = $latestText; available = $latestText }
    Say "download failed: $($_.Exception.Message)"
    exit 1
}
$want = $null
foreach ($line in ($sumText -split "`r?`n")) {
    $m = [regex]::Match($line.Trim(), "^([0-9a-fA-F]{64})\s+\*?(.+)$")
    if ($m.Success -and $m.Groups[2].Value.Trim() -eq $asset.name) { $want = $m.Groups[1].Value.ToLower() }
}
$got = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
if (-not $want -or $want -ne $got) {
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    Save-Status "error" "The downloaded Setup does not match its SHA-256 in the release; nothing installed." @{ latest = $latestText; available = $latestText }
    Say "SHA-256 mismatch: want $want got $got"
    exit 1
}
Say "SHA-256 ok ($got)"

# ---- 4. install, check, go back if it does not start -----------------------------------------------------------
if (Test-Path $SetupNow) { Copy-Item $SetupNow $SetupPrev -Force }
Save-Status "installing" "Installing version $latestText (the collector stops for about a minute)..." @{ latest = $latestText; available = $latestText }
Say "installing $latestText"
$p = Start-Process $exe -ArgumentList "/S", "/UPDATE" -Wait -PassThru
$rc = $p.ExitCode
$now = Get-Installed
$up = Wait-Up 180
if ($rc -eq 0 -and $up -and (Get-Ver $now) -eq $latest) {
    Save-Status "up to date" "Version $latestText installed $(Get-Date -Format 'yyyy-MM-dd HH:mm')." @{ latest = $latestText; available = $null; last_install = "$latestText at $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
    Say "installed $latestText, the service answers"
    Remove-Item -Recurse -Force $dl -ErrorAction SilentlyContinue
    exit 0
}
Say "the update did not come up (Setup exit code $rc, service answers: $up, version now $now)"
if (Test-Path $SetupPrev) {
    Say "putting the previous version back"
    $p2 = Start-Process $SetupPrev -ArgumentList "/S", "/UPDATE" -Wait -PassThru
    $back = Wait-Up 180
    try {
        $us = Join-Path $UpdDir "settings.json"
        $j = Get-Content $us -Raw | ConvertFrom-Json
        $h = @{}; foreach ($pp in $j.PSObject.Properties) { $h[$pp.Name] = $pp.Value }
        $h["skip"] = $latestText
        ($h | ConvertTo-Json) | Set-Content -Path $us -Encoding UTF8
    } catch {}
    Save-Status "error" "Version $latestText did not start on this PC; version $(Get-Installed) was put back ($(if ($back) { 'it answers' } else { 'it does NOT answer yet: restart the PC' }))." @{ latest = $latestText; available = $latestText }
    Say "previous version back (exit $($p2.ExitCode), answers: $back)"
    exit 2
}
try { Start-ScheduledTask -TaskName "PlantDataCollector" } catch {}
Save-Status "error" "Version $latestText was installed but the service did not answer within 3 minutes; see $LogFile and C:\ProgramData\CCI Plant Monitoring\install.log." @{ latest = $latestText; available = $latestText }
exit 2
