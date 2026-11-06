# The auto-updater: Windows task "PlantCollectorUpdater" (as SYSTEM; the Setup registers it). It runs every 4 hours,
# 10 minutes after Windows starts, and at once when "Check for updates" / "Install now" is clicked in the dashboard
# (Settings > About: the collector drops now.flag in the update folder and starts this task).
#
#   1. asks GitHub for the newest version of the program: a published Release of Tishkos/applicationCocaCola, or the
#      Setup on its branch "setup-download" (named in that branch's SHA256SUMS.txt), whichever is newer. Plain web
#      requests: no GitHub program, account or sign-in is needed on the PC, only internet.
#   2. newer than the installed version (version.txt)?
#        - installs it when "Install now" was clicked, or automatically at night (01:00-05:00, the PC's own clock)
#          when automatic updates are on; otherwise only reports "available" in the dashboard
#        - "Download" (1.0.8 and later) only downloads and checks it, and reports "ready": the dashboard then offers
#          "Install", which uses the downloaded Setup at once
#   3. installs: downloads that CCI-Plant-Monitoring-Setup-<version>.exe and its SHA256SUMS.txt, checks the
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
$Repo = "Tishkos/applicationCocaCola"                    # fixed here, never read from a file
$Link = [IO.FileAttributes]::ReparsePoint

function Remove-Tree($path) {
    # deletes a folder without ever following a junction or symbolic link inside it (they are deleted as links)
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return }
    if ($item.Attributes -band $Link) {
        if ($item.PSIsContainer) { [IO.Directory]::Delete($item.FullName, $false) } else { [IO.File]::Delete($item.FullName) }
        return
    }
    if ($item.PSIsContainer) {
        foreach ($c in @(Get-ChildItem -LiteralPath $item.FullName -Force -ErrorAction SilentlyContinue)) { Remove-Tree $c.FullName }
        [IO.Directory]::Delete($item.FullName, $false)
    } else {
        $item.Attributes = "Normal"
        [IO.File]::Delete($item.FullName)
    }
}

function Say($m) {
    try {
        if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) { Move-Item $LogFile "$LogFile.old" -Force }
        Add-Content -Path $LogFile -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8
    } catch {}
    Write-Output $m
}

$cfg = @{ auto = $true; window_start_hour = 1; window_end_hour = 5; skip = "" }
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
    for ($i = 0; $i -lt 10; $i++) {                       # the collector may be reading it this very moment
        try { Move-Item $tmp $StatusFile -Force -ErrorAction Stop; return } catch { Start-Sleep -Milliseconds 300 }
    }
    try { Copy-Item $tmp $StatusFile -Force -ErrorAction Stop; Remove-Item $tmp -Force -ErrorAction SilentlyContinue } catch {}
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

function Run-Setup($path) {
    # the Setup itself, waited for on its own: Start-Process -Wait would also wait for every process it started (and
    # anything those leave running), which kept the updater from ever reporting back. 20 minutes at the most.
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $path
    $psi.Arguments = "/S /UPDATE"
    $psi.UseShellExecute = $false
    $proc = [Diagnostics.Process]::Start($psi)
    if (-not $proc.WaitForExit(20 * 60 * 1000)) {
        Say "the Setup did not end within 20 minutes: stopped" | Out-Null
        & "$env:SystemRoot\System32\taskkill.exe" /T /F /PID $proc.Id 2>&1 | Out-Null     # it holds the Setup's lock
        Start-Sleep -Seconds 3
        return -1
    }
    return $proc.ExitCode
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
    $age = ((Get-Date) - (Get-Item $Flag).LastWriteTime).TotalMinutes
    # "Install now" counts only when it was clicked in the last 15 minutes; an older one is a check (never an
    # install in the middle of the day hours after the click)
    $mode = if ("$f" -like "install*" -and $age -le 15) { "install" } elseif ("$f" -like "download*" -and $age -le 15) { "download" } else { "check" }
    Remove-Item $Flag -Force -ErrorAction SilentlyContinue
}
$installed = Get-Installed
Say "--- check ($mode), installed $installed, repository $Repo"

# ---- 1. the newest version: a published GitHub Release, or the Setup on the branch "setup-download" ------------
# Plain web requests to github.com, like a browser: no GitHub program, account or sign-in is needed on this PC.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"
$ua = "CCI-Plant-Monitoring-Updater/$installed"
$headers = @{ "User-Agent" = $ua; "Accept" = "application/vnd.github+json" }
$Raw = "https://raw.githubusercontent.com/$Repo/setup-download"
$SetupName = "^CCI-Plant-Monitoring-Setup-(\d+\.\d+\.\d+)\.exe$"

function Get-Text($url) {
    $c = (Invoke-WebRequest $url -Headers @{ "User-Agent" = $ua } -TimeoutSec 60 -UseBasicParsing).Content
    if ($c -is [byte[]]) { $c = [Text.Encoding]::UTF8.GetString($c) }
    return "$c"
}

function Read-Sums($text) {
    # "<sha256>  CCI-Plant-Monitoring-Setup-1.2.3.exe" lines -> @{ file name = sha256 }
    $out = @{}
    foreach ($line in ("$text" -split "`r?`n")) {
        $m = [regex]::Match($line.Trim(), "^([0-9a-fA-F]{64})\s+\*?(\S+)$")
        if ($m.Success -and $m.Groups[2].Value -match $SetupName) { $out[$m.Groups[2].Value] = $m.Groups[1].Value.ToLower() }
    }
    return $out
}

function Get-Code($err) {
    try { return [int]$err.Exception.Response.StatusCode } catch { return $null }
}

$cands = @()
$reached = $false
$lastErr = ""
# a) a published GitHub Release (made by the release workflow from a tag v1.2.3)
try {
    $rel = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers $headers -TimeoutSec 30 -UseBasicParsing
    $reached = $true
    $asset = @($rel.assets | Where-Object { $_.name -match $SetupName }) | Select-Object -First 1
    $sums = @($rel.assets | Where-Object { $_.name -eq "SHA256SUMS.txt" }) | Select-Object -First 1
    $v = Get-Ver "$($rel.tag_name)"
    if ($asset -and $sums -and $v) {
        $cands += @{ version = $v; name = $asset.name; url = $asset.browser_download_url; sums = $sums.browser_download_url; from = "the GitHub release $($rel.tag_name)" }
    }
} catch {
    if ((Get-Code $_) -eq 404) { $reached = $true } else { $lastErr = $_.Exception.Message }
}
# b) the Setup on the download branch (its SHA256SUMS.txt names it)
try {
    $branchSums = Read-Sums (Get-Text "$Raw/SHA256SUMS.txt")
    $reached = $true
    foreach ($name in @($branchSums.Keys)) {
        $v = Get-Ver ([regex]::Match($name, $SetupName).Groups[1].Value)
        if ($v) { $cands += @{ version = $v; name = $name; url = "$Raw/$name"; sums = "$Raw/SHA256SUMS.txt"; from = "the download branch" } }
    }
} catch {
    if ((Get-Code $_) -eq 404) { $reached = $true } elseif (-not $lastErr) { $lastErr = $_.Exception.Message }
}
if (-not $reached) {
    Save-Status "error" ("Could not reach GitHub: " + $lastErr + " (the next check is in 4 hours).")
    Say "GitHub not reachable: $lastErr"
    exit 1
}
$best = @($cands | Sort-Object -Property @{ Expression = { $_.version } } -Descending) | Select-Object -First 1
$have = Get-Ver $installed
if (-not $best) {
    Save-Status "up to date" "No version is published on GitHub yet; $installed is installed." @{ latest = $null; available = $null }
    Say "nothing published yet"
    exit 0
}
$latest = $best.version
$latestText = $latest.ToString()
if (-not $have -or $latest -le $have) {
    Save-Status "up to date" "$installed is the newest version." @{ latest = $latestText; available = $null }
    Say "up to date (newest on GitHub: $latestText, from $($best.from))"
    exit 0
}
$dl = Join-Path $UpdDir "download"
$exe = Join-Path $dl $best.name
$exeSum = $exe + ".sha256"
function Test-Downloaded {
    # the Setup of this version is already in the download folder and matches its SHA-256: the published one, or,
    # when GitHub does not answer just now, the one written next to it when it was downloaded and checked (a
    # network error must never throw a good download away)
    if (-not (Test-Path $exe)) { return $false }
    $w = $null
    try { $w = (Read-Sums (Get-Text $best.sums))[$best.name] } catch { $w = $null }
    if (-not $w -and (Test-Path $exeSum)) {
        try { $w = ("" + (Get-Content $exeSum -TotalCount 1)).Trim().ToLower() } catch { $w = $null }
    }
    if (-not $w) { return $false }
    try { return [bool]((Get-FileHash $exe -Algorithm SHA256).Hash.ToLower() -eq $w) } catch { return $false }
}
if ("$($cfg.skip)" -eq $latestText -and $mode -notin "install", "download") {
    # it failed on this PC before: never installed by itself, but Download and Install from the dashboard still work
    if (Test-Downloaded) {
        Save-Status "ready" "Version $latestText is downloaded and checked. It did not start on this PC last time, so it is installed only with 'Install'." @{ latest = $latestText; available = $latestText }
    } else {
        Save-Status "update available" "Version $latestText did not start on this PC last time and was taken back; it is installed only with 'Download' and then 'Install'." @{ latest = $latestText; available = $latestText }
    }
    Say "$latestText is on the skip list (it failed before)"
    exit 0
}

# ---- 2. install now? ---------------------------------------------------------------------------------------------
$hour = (Get-Date).Hour
$inWindow = ($hour -ge [int]$cfg.window_start_hour -and $hour -lt [int]$cfg.window_end_hour)
$installNow = ($mode -eq "install") -or ($mode -ne "download" -and [bool]$cfg.auto -and $inWindow)
$go = $installNow -or ($mode -eq "download")
if (-not $go -and (Test-Downloaded)) {
    Save-Status "ready" "Version $latestText is downloaded and checked: 'Install' installs it now." @{ latest = $latestText; available = $latestText }
    Say "ready: $latestText (downloaded earlier, not installing now)"
    exit 0
}
if (-not $go) {
    $when = if ([bool]$cfg.auto) { "It is installed automatically tonight between $window, or now with 'Download' and then 'Install'." } else { "Automatic updates are off: use 'Download' and then 'Install'." }
    Save-Status "update available" "Version $latestText is available (installed: $installed). $when" @{ latest = $latestText; available = $latestText }
    Say "available: $latestText (not installing now)"
    exit 0
}

# ---- 3. download and check (unless it is already here and checked) -----------------------------------------------
if (Test-Downloaded) {
    Say "already downloaded and checked: $($best.name)"
} else {
    Remove-Tree $dl
    New-Item -ItemType Directory -Force $dl | Out-Null
    Save-Status "downloading" "Downloading version $latestText..." @{ latest = $latestText; available = $latestText }
    Say "downloading $($best.name) from $($best.from)"
    try {
        Invoke-WebRequest $best.url -OutFile $exe -Headers @{ "User-Agent" = $ua } -TimeoutSec 600 -UseBasicParsing
        $want = (Read-Sums (Get-Text $best.sums))[$best.name]
    } catch {
        Save-Status "error" ("Download failed: " + $_.Exception.Message + "; nothing installed.") @{ latest = $latestText; available = $latestText }
        Say "download failed: $($_.Exception.Message)"
        exit 1
    }
    $got = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
    if (-not $want -or $want -ne $got) {
        Remove-Item $exe -Force -ErrorAction SilentlyContinue
        Save-Status "error" "The downloaded Setup does not match its SHA-256 in SHA256SUMS.txt; nothing installed (the next check is in 4 hours)." @{ latest = $latestText; available = $latestText }
        Say "SHA-256 mismatch: want $want got $got"
        exit 1
    }
    Say "SHA-256 ok ($got)"
    try { Set-Content -Path $exeSum -Value $got -Encoding ASCII } catch {}
}
if (-not $installNow) {
    Save-Status "ready" "Version $latestText is downloaded and checked: 'Install' installs it now." @{ latest = $latestText; available = $latestText }
    Say "ready: $latestText (downloaded, waiting for 'Install')"
    exit 0
}

# ---- 4. install, check, go back if it does not start -----------------------------------------------------------
if (Test-Path $SetupNow) { Copy-Item $SetupNow $SetupPrev -Force }
Save-Status "installing" "Installing version $latestText (the collector stops for about a minute)..." @{ latest = $latestText; available = $latestText }
Say "installing $latestText"
$rc = Run-Setup $exe
$now = Get-Installed
$up = Wait-Up 180
# the Setup ends with 3 when the service needed more than its 2 minutes to answer: fine if it answers now
if (($rc -eq 0 -or $rc -eq 3) -and $up -and (Get-Ver $now) -eq $latest) {
    Save-Status "up to date" "Version $latestText installed $(Get-Date -Format 'yyyy-MM-dd HH:mm')." @{ latest = $latestText; available = $null; last_install = "$latestText at $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
    Say "installed $latestText, the service answers"
    try { Remove-Tree $dl } catch {}
    exit 0
}
Say "the update did not come up (Setup exit code $rc, service answers: $up, version now $now)"
if (Test-Path $SetupPrev) {
    Say "putting the previous version back"
    $rc2 = Run-Setup $SetupPrev
    $back = Wait-Up 180
    try {
        $us = Join-Path $UpdDir "settings.json"
        $j = Get-Content $us -Raw | ConvertFrom-Json
        $h = @{}; foreach ($pp in $j.PSObject.Properties) { $h[$pp.Name] = $pp.Value }
        $h["skip"] = $latestText
        ($h | ConvertTo-Json) | Set-Content -Path $us -Encoding UTF8
    } catch {}
    Save-Status "error" "Version $latestText did not start on this PC; version $(Get-Installed) was put back ($(if ($back) { 'it answers' } else { 'it does NOT answer yet: restart the PC' }))." @{ latest = $latestText; available = $latestText }
    Say "previous version back (exit $rc2, answers: $back)"
    exit 2
}
try { Start-ScheduledTask -TaskName "PlantDataCollector" } catch {}
Save-Status "error" "Version $latestText was installed but the service did not answer within 3 minutes; see $LogFile and C:\ProgramData\CCI Plant Monitoring\install.log." @{ latest = $latestText; available = $latestText }
exit 2
