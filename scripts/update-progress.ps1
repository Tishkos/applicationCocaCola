# The update window: shown when "Install now" is clicked in Settings > Updates (app\desktop.py starts it from a copy
# in %TEMP% and closes the program window). It follows the updater (C:\ProgramData\CCI Plant Monitoring\update\
# status.json): downloading, installing, starting; when the new version answers it opens CCI Plant Monitoring again
# and closes. Runs as the signed-in user, changes nothing, only reads.
param([string]$InstallDir = "", [string]$Version = "")
$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$created = $false
$mutex = [System.Threading.Mutex]::new($true, "Local\CCI.PlantMonitoring.updateWindow", [ref]$created)
if (-not $created) { exit 0 }                          # one update window at a time

if (-not $InstallDir) { $InstallDir = Join-Path $env:ProgramFiles "CCI Plant Monitoring" }
$Pd = Join-Path $env:ProgramData "CCI Plant Monitoring"
$StatusFile = Join-Path $Pd "update\status.json"
$Marker = Join-Path $Pd "update\installing.flag"
$VersionFile = Join-Path $InstallDir "version.txt"
$AppExe = Join-Path $InstallDir "CCI Plant Monitoring.exe"
$from = ""
try { $from = (Get-Content $VersionFile -TotalCount 1).Trim() } catch {}
$started = Get-Date

function Read-Status {
    # only what the updater wrote after the click: an older state (last night's error, an earlier "up to date") is
    # not about this update
    try {
        if ((Get-Item $StatusFile -ErrorAction Stop).LastWriteTime -lt $started.AddSeconds(-2)) { return $null }
        return (Get-Content $StatusFile -Raw -ErrorAction Stop | ConvertFrom-Json)
    } catch { return $null }
}
function Installed {
    try { return (Get-Content $VersionFile -TotalCount 1 -ErrorAction Stop).Trim() } catch { return "" }
}
function Answers {
    $url = "http://127.0.0.1:8080/"
    try {
        $f = Join-Path $Pd "data\dashboard.url"
        if (Test-Path $f) { $t = (Get-Content $f -TotalCount 1).Trim().Replace("localhost", "127.0.0.1"); if ($t) { $url = $t.TrimEnd("/") + "/" } }
    } catch {}
    try { Invoke-RestMethod ($url + "api/v1/health") -TimeoutSec 2 -UseBasicParsing | Out-Null; return $true } catch { return $false }
}

# ---- the window -------------------------------------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "CCI Plant Monitoring update"
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.ClientSize = New-Object System.Drawing.Size(460, 190)
$form.BackColor = [System.Drawing.Color]::FromArgb(24, 25, 31)
$form.ForeColor = [System.Drawing.Color]::FromArgb(229, 231, 235)
$form.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$form.TopMost = $true
try {
    $bytes = [IO.File]::ReadAllBytes((Join-Path $InstallDir "app\PlantCollector.ico"))     # into memory: the file is replaced meanwhile
    $form.Icon = New-Object System.Drawing.Icon((New-Object IO.MemoryStream(, $bytes)))
} catch {}

$title = New-Object System.Windows.Forms.Label
$title.AutoSize = $false
$title.SetBounds(24, 20, 412, 30)
$title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 13)
$title.Text = if ($Version) { "Updating to version $Version" } else { "Updating CCI Plant Monitoring" }
$form.Controls.Add($title)

$text = New-Object System.Windows.Forms.Label
$text.AutoSize = $false
$text.SetBounds(24, 56, 412, 44)
$text.ForeColor = [System.Drawing.Color]::FromArgb(154, 163, 178)
$text.Text = "Asking the updater..."
$form.Controls.Add($text)

$bar = New-Object System.Windows.Forms.ProgressBar
$bar.SetBounds(24, 108, 412, 14)
$bar.Style = "Marquee"
$bar.MarqueeAnimationSpeed = 30
$form.Controls.Add($bar)

$btnOpen = New-Object System.Windows.Forms.Button
$btnOpen.Text = "Open CCI Plant Monitoring"
$btnOpen.SetBounds(196, 140, 170, 30)
$btnOpen.FlatStyle = "System"
$btnOpen.Visible = $false
$form.Controls.Add($btnOpen)

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text = "Close"
$btnClose.SetBounds(372, 140, 64, 30)
$btnClose.FlatStyle = "System"
$btnClose.Visible = $false
$form.Controls.Add($btnClose)

function Open-App {
    if (Test-Path $AppExe) { try { Start-Process -FilePath $AppExe } catch {} }
}
$btnOpen.Add_Click({ Open-App; $form.Close() })
$btnClose.Add_Click({ $form.Close() })

function Finish($ok, $message) {
    $timer.Stop()
    $bar.Style = "Continuous"
    $bar.Value = if ($ok) { 100 } else { 0 }
    $text.Text = $message
    $btnOpen.Visible = $true
    $btnClose.Visible = $true
}

$state = @{ done = $false; doneAt = $null; working = $false }
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({
    if (((Get-Date) - $started).TotalMinutes -gt 15) {
        Finish $false "The update takes longer than expected. It goes on by itself; the details are in C:\ProgramData\CCI Plant Monitoring\update\updater.log."
        return
    }
    $st = Read-Status
    $now = Installed
    $markerOn = $false
    try { $markerOn = (Test-Path $Marker) -and (((Get-Date) - (Get-Item $Marker).LastWriteTime).TotalMinutes -lt 30) } catch {}
    # any version other than the one that ran at the click (the updater may have found an even newer one)
    $newVer = $now -and $from -and ($now -ne $from)
    if ($newVer -and -not $markerOn) {
        if (Answers) {
            if (-not $state.done) {
                $state.done = $true; $state.doneAt = Get-Date
                $title.Text = "Version $now is installed"
                $text.Text = "Opening CCI Plant Monitoring..."
                $bar.Style = "Continuous"; $bar.Value = 100
            } elseif (((Get-Date) - $state.doneAt).TotalSeconds -ge 2) {
                $timer.Stop()
                Open-App
                $form.Close()
            }
            return
        }
        $text.Text = "Version $now is installed. Starting the background service..."
        return
    }
    if ($markerOn) { $state.working = $true }
    if ($st) {
        switch ("$($st.state)") {
            "downloading" { $state.working = $true; $text.Text = "Downloading version $($st.latest)..." }
            "installing"  { $state.working = $true; $text.Text = "Installing version $($st.latest). The collector stops for about a minute; the window opens again by itself." }
            "error"       { if (-not $markerOn) { Finish $false ("The update did not finish: " + $st.message) } }
            # final only before any download or installation was seen (afterwards: a version change, or an error)
            "up to date"  { if (-not $state.working -and -not $markerOn) { Finish $true ("Nothing to install: " + $st.message) } }
            default       { $text.Text = "Asking the updater..." }
        }
    }
})
$form.Add_Shown({ $timer.Start(); $form.Activate() })
[void]$form.ShowDialog()
$mutex.ReleaseMutex()
