# The update window: shown when "Install" is clicked in Settings > Updates (app\desktop.py starts it and closes the
# program window, so none of the program's files is in use). It follows the updater (C:\ProgramData\CCI Plant
# Monitoring\update\status.json): download (when it was not downloaded before), installation, the collector starting
# again. When the new version answers it says so and offers "Reopen CCI Plant Monitoring". Dark, like the program
# window. Runs as the signed-in user, changes nothing, only reads. ASCII only (Windows PowerShell 5.1 reads a script
# without a BOM as ANSI): the symbols are drawn, or written as [char] codes.
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
$LogPath = Join-Path $Pd "update\updater.log"
$from = ""
try { $from = (Get-Content $VersionFile -TotalCount 1).Trim() } catch {}
$started = Get-Date

function Read-Status([switch]$Any) {
    # only what the updater wrote after the click (an older state is not about this update), unless -Any
    try {
        if (-not $Any -and (Get-Item $StatusFile -ErrorAction Stop).LastWriteTime -lt $started.AddSeconds(-2)) { return $null }
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

# ---- colours and fonts of the program window -----------------------------------------------------------------------
function Rgb($r, $g, $b) { return [System.Drawing.Color]::FromArgb($r, $g, $b) }
$C = @{
    bg = (Rgb 17 18 23); panel = (Rgb 24 27 31); panel2 = (Rgb 31 34 40); panel3 = (Rgb 40 43 50); border = (Rgb 45 48 54)
    text = (Rgb 204 204 220); text2 = (Rgb 150 151 163); text3 = (Rgb 104 105 115); strong = (Rgb 255 255 255)
    cci = (Rgb 224 47 68); cciHover = (Rgb 238 66 86); ok = (Rgb 115 191 105); bad = (Rgb 242 73 92)
}
$F = @{
    title = New-Object System.Drawing.Font("Segoe UI Semibold", 15)
    head = New-Object System.Drawing.Font("Segoe UI Semibold", 11)
    text = New-Object System.Drawing.Font("Segoe UI", 10)
    small = New-Object System.Drawing.Font("Segoe UI", 9)
    btn = New-Object System.Drawing.Font("Segoe UI Semibold", 10)
    glyph = New-Object System.Drawing.Font("Segoe UI Symbol", 11)
}
$G = @{ wait = [string][char]0x25CB; now = [string][char]0x25CF; done = [string][char]0x2713 }   # circle, dot, tick

function Set-DoubleBuffered($control) {
    try { $control.GetType().GetProperty("DoubleBuffered", [Reflection.BindingFlags]"NonPublic,Instance").SetValue($control, $true, $null) } catch {}
}
function New-Label($x, $y, $w, $h, $font, $color, $text) {
    $l = New-Object System.Windows.Forms.Label
    $l.AutoSize = $false
    $l.SetBounds($x, $y, $w, $h)
    $l.Font = $font
    $l.ForeColor = $color
    $l.BackColor = [System.Drawing.Color]::Transparent
    $l.Text = $text
    return $l
}
function New-Btn($text, $primary) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text
    $b.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $b.Font = $F.btn
    $b.ForeColor = $C.strong
    $b.Cursor = [System.Windows.Forms.Cursors]::Hand
    $b.Visible = $false
    if ($primary) {
        $b.BackColor = $C.cci
        $b.FlatAppearance.BorderSize = 0
        $b.FlatAppearance.MouseOverBackColor = $C.cciHover
    } else {
        $b.BackColor = $C.panel2
        $b.FlatAppearance.BorderSize = 1
        $b.FlatAppearance.BorderColor = $C.panel3
        $b.FlatAppearance.MouseOverBackColor = $C.panel3
    }
    return $b
}

# ---- the window -----------------------------------------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "CCI Plant Monitoring update"
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.ClientSize = New-Object System.Drawing.Size(560, 360)
$form.BackColor = $C.bg
$form.ForeColor = $C.text
$form.Font = $F.text
$form.TopMost = $true
Set-DoubleBuffered $form
try {
    $bytes = [IO.File]::ReadAllBytes((Join-Path $InstallDir "app\PlantCollector.ico"))     # into memory: the file is replaced meanwhile
    $form.Icon = New-Object System.Drawing.Icon((New-Object IO.MemoryStream(, $bytes)))
} catch {}
# a dark title bar, as in the program window (Windows 10 2004 and later; nothing happens on older ones)
try {
    Add-Type -Namespace CCIUpdate -Name Dwm -MemberDefinition '[DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);' -ErrorAction Stop
    $form.Add_HandleCreated({
        try {
            $on = 1
            if ([CCIUpdate.Dwm]::DwmSetWindowAttribute($form.Handle, 20, [ref]$on, 4) -ne 0) { [void][CCIUpdate.Dwm]::DwmSetWindowAttribute($form.Handle, 19, [ref]$on, 4) }
        } catch {}
    })
} catch {}

# header: the program's icon and name
$head = New-Object System.Windows.Forms.Panel
$head.SetBounds(0, 0, 560, 58)
$head.BackColor = $C.panel
$form.Controls.Add($head)
$logo = New-Object System.Windows.Forms.PictureBox
$logo.SetBounds(18, 13, 32, 32)
$logo.SizeMode = "Zoom"
$logo.BackColor = [System.Drawing.Color]::Transparent
try {
    $pbytes = [IO.File]::ReadAllBytes((Join-Path $InstallDir "app\PlantCollector.png"))
    $script:logoStream = New-Object IO.MemoryStream(, $pbytes)                           # kept open for the image
    $logo.Image = [System.Drawing.Image]::FromStream($script:logoStream)
} catch {}
$head.Controls.Add($logo)
$head.Controls.Add((New-Label 60 10 380 22 $F.head $C.strong "CCI Plant Monitoring"))
$head.Controls.Add((New-Label 60 31 380 18 $F.small $C.text3 "Update"))
$line = New-Object System.Windows.Forms.Panel
$line.SetBounds(0, 58, 560, 1)
$line.BackColor = $C.border
$form.Controls.Add($line)

# the indicator: a spinning ring while it works, a tick or an exclamation mark at the end
$ui = @{ angle = 0; phase = "work"; t = 0 }
$ind = New-Object System.Windows.Forms.Panel
$ind.SetBounds(26, 82, 64, 64)
$ind.BackColor = $C.bg
Set-DoubleBuffered $ind
$ind.Add_Paint({
    param($sender, $e)
    $g = $e.Graphics
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $r = [System.Drawing.Rectangle]::new(5, 5, 53, 53)
    if ($ui.phase -eq "work") {
        $p1 = New-Object System.Drawing.Pen($C.panel3, 5)
        $g.DrawEllipse($p1, $r)
        $p2 = New-Object System.Drawing.Pen($C.cci, 5)
        $p2.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $p2.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $g.DrawArc($p2, $r, [single]$ui.angle, [single]110)
        $p1.Dispose(); $p2.Dispose()
    } else {
        $col = $C.bad
        if ($ui.phase -eq "ok") { $col = $C.ok }
        $b = New-Object System.Drawing.SolidBrush($col)
        $g.FillEllipse($b, $r)
        $b.Dispose()
        $w = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 5)
        $w.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $w.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $w.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
        if ($ui.phase -eq "ok") {
            $pts = [System.Drawing.Point[]]@([System.Drawing.Point]::new(19, 32), [System.Drawing.Point]::new(28, 41), [System.Drawing.Point]::new(45, 23))
            $g.DrawLines($w, $pts)
        } else {
            $g.DrawLine($w, 31, 17, 31, 35)
            $dot = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
            $g.FillEllipse($dot, 28, 41, 7, 7)
            $dot.Dispose()
        }
        $w.Dispose()
    }
})
$form.Controls.Add($ind)

$title = New-Label 108 80 430 32 $F.title $C.strong ""
$title.Text = if ($Version) { "Updating to version $Version" } else { "Updating CCI Plant Monitoring" }
$form.Controls.Add($title)
$text = New-Label 108 114 430 44 $F.text $C.text2 "Asking the updater..."
$form.Controls.Add($text)

# the three steps
$vtxt = if ($Version) { "version $Version" } else { "the new version" }
$steps = @(
    @{ glyph = (New-Label 108 170 22 22 $F.glyph $C.text3 $G.wait); label = (New-Label 132 171 400 22 $F.text $C.text2 "Download and check $vtxt") },
    @{ glyph = (New-Label 108 198 22 22 $F.glyph $C.text3 $G.wait); label = (New-Label 132 199 400 22 $F.text $C.text2 "Install $vtxt (the collector stops for about a minute)") },
    @{ glyph = (New-Label 108 226 22 22 $F.glyph $C.text3 $G.wait); label = (New-Label 132 227 400 22 $F.text $C.text2 "Start the collector and check that it answers") }
)
foreach ($s in $steps) { $form.Controls.Add($s.glyph); $form.Controls.Add($s.label) }
function Set-Step([int]$n) {
    # steps before n done, step n working, the rest waiting (n = 3: all done)
    for ($i = 0; $i -lt 3; $i++) {
        $s = $steps[$i]
        if ($i -lt $n) { $s.glyph.Text = $G.done; $s.glyph.ForeColor = $C.ok; $s.label.ForeColor = $C.text }
        elseif ($i -eq $n) { $s.glyph.Text = $G.now; $s.glyph.ForeColor = $C.cci; $s.label.ForeColor = $C.strong }
        else { $s.glyph.Text = $G.wait; $s.glyph.ForeColor = $C.text3; $s.label.ForeColor = $C.text2 }
    }
}

# a thin moving bar under the steps
$bar = New-Object System.Windows.Forms.Panel
$bar.SetBounds(26, 268, 508, 4)
$bar.BackColor = $C.panel3
Set-DoubleBuffered $bar
$bar.Add_Paint({
    param($sender, $e)
    $w = $bar.Width
    if ($ui.phase -eq "work") {
        $x = (($ui.t * 7) % ($w + 140)) - 140
        $b = New-Object System.Drawing.SolidBrush($C.cci)
        $e.Graphics.FillRectangle($b, $x, 0, 140, 4)
        $b.Dispose()
    } else {
        $col = $C.bad
        if ($ui.phase -eq "ok") { $col = $C.ok }
        $b = New-Object System.Drawing.SolidBrush($col)
        $e.Graphics.FillRectangle($b, 0, 0, $w, 4)
        $b.Dispose()
    }
})
$form.Controls.Add($bar)

$btnOpen = New-Btn "Reopen CCI Plant Monitoring" $true
$btnOpen.SetBounds(254, 300, 210, 36)
$form.Controls.Add($btnOpen)
$btnClose = New-Btn "Close" $false
$btnClose.SetBounds(472, 300, 62, 36)
$form.Controls.Add($btnClose)
$hint = New-Label 26 304 220 30 $F.small $C.text3 "You can close this window: the update goes on by itself."
$form.Controls.Add($hint)

function Open-App {
    if (Test-Path $AppExe) { try { Start-Process -FilePath $AppExe } catch {} }
}
$btnOpen.Add_Click({ Open-App; $form.Close() })
$btnClose.Add_Click({ $form.Close() })

function Finish($ok, $head, $message) {
    $tick.Stop()
    $ui.phase = if ($ok) { "ok" } else { "bad" }
    $title.Text = $head
    $text.Text = $message
    if ($ok) { Set-Step 3 }
    $btnOpen.Text = if ($ok) { "Reopen CCI Plant Monitoring" } else { "Open CCI Plant Monitoring" }
    $btnOpen.Visible = $true
    $btnClose.Visible = $true
    $hint.Visible = $false
    $ind.Invalidate(); $bar.Invalidate()
    $form.TopMost = $true
    $form.Activate()
    [void]$btnOpen.Focus()
}

# the animation: about 30 frames a second
$anim = New-Object System.Windows.Forms.Timer
$anim.Interval = 33
$anim.Add_Tick({
    if ($ui.phase -ne "work") { $anim.Stop(); return }
    $ui.angle = ($ui.angle + 10) % 360
    $ui.t = $ui.t + 1
    $ind.Invalidate(); $bar.Invalidate()
})

# the state: once a second
$state = @{ done = $false; working = $false; step = 0 }
$first = Read-Status -Any
if ($first -and "$($first.state)" -eq "ready") { $state.step = 1 }          # downloaded and checked before the click
Set-Step $state.step
$tick = New-Object System.Windows.Forms.Timer
$tick.Interval = 1000
$tick.Add_Tick({
    if (((Get-Date) - $started).TotalMinutes -gt 15) {
        Finish $false "It takes longer than expected" "The update goes on by itself. The details are in $LogPath."
        return
    }
    $st = Read-Status
    $now = Installed
    $markerOn = $false
    try { $markerOn = (Test-Path $Marker) -and (((Get-Date) - (Get-Item $Marker).LastWriteTime).TotalMinutes -lt 30) } catch {}
    # any version other than the one that ran at the click (the updater may have found an even newer one)
    $newVer = $now -and $from -and ($now -ne $from)
    if ($newVer -and -not $markerOn) {
        if ($state.step -lt 2) { $state.step = 2; Set-Step 2 }
        if (Answers) {
            Finish $true "Version $now is installed" ("Everything is ready, finished at " + (Get-Date -Format "HH:mm") + ". Settings and history were kept. Click Reopen to open CCI Plant Monitoring.")
            return
        }
        $text.Text = "Version $now is installed. Starting the background service..."
        return
    }
    if ($markerOn) {
        $state.working = $true
        if ($state.step -lt 1) { $state.step = 1; Set-Step 1 }
        $text.Text = "Installing. The collector stops for about a minute."
        return
    }
    if ($st) {
        switch ("$($st.state)") {
            "downloading" { $state.working = $true; $text.Text = "Downloading version $($st.latest)..." }
            "installing"  {
                $state.working = $true
                if ($state.step -lt 1) { $state.step = 1; Set-Step 1 }
                $text.Text = "Installing version $($st.latest). The collector stops for about a minute."
            }
            "error"       { Finish $false "The update did not finish" ("" + $st.message) }
            # final only before any download or installation was seen (afterwards: a version change, or an error)
            "up to date"  { if (-not $state.working) { Finish $true "Nothing to install" ("" + $st.message) } }
            default       { $text.Text = "Asking the updater..." }
        }
    }
})
$form.Add_Shown({ $anim.Start(); $tick.Start(); $form.Activate() })
[void]$form.ShowDialog()
$mutex.ReleaseMutex()
