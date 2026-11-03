# Run by the uninstaller (Settings > Apps > CCI Plant Monitoring > Uninstall), as administrator.
# Removes everything the Setup put on the PC except the program files themselves (the uninstaller deletes those
# after this script):
#   the background service (task PlantDataCollector) and the updater (task PlantCollectorUpdater), every collector
#   and window process, the firewall rules, the shortcuts (all users and the current user).
# -DeleteData also deletes C:\ProgramData\CCI Plant Monitoring (settings, the collected data, logs, updater files).
# Without it they stay, so a later Setup carries on with the same settings and history.
# The program's own Python (runtime\) goes with the program folder.
param([switch]$DeleteData, [string]$InstallDir = "")
$ErrorActionPreference = "Continue"
if (-not $InstallDir) { $InstallDir = Split-Path -Parent $PSScriptRoot }
$Pd = Join-Path $env:ProgramData "CCI Plant Monitoring"
function Say($m) { Write-Output $m }
$Link = [IO.FileAttributes]::ReparsePoint

function Remove-Tree($path) {
    # deletes a folder without ever following a junction or symbolic link inside it (they are deleted as links):
    # Remove-Item -Recurse in Windows PowerShell 5.1 would delete what a junction points to
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return }
    try {
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
    } catch {}
}

foreach ($t in "PlantDataCollector", "PlantCollectorUpdater") {
    try { Stop-ScheduledTask -TaskName $t -ErrorAction Stop } catch {}
    try { Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction Stop; Say "removed the Windows task $t" } catch {}
}
$procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    ($_.Name -eq "CCI Plant Monitoring Service.exe") -or ($_.Name -eq "CCI Plant Monitoring.exe") -or
    ($_.CommandLine -and ($_.CommandLine -like "*plantcollector.cli*" -or $_.CommandLine -like "*app\desktop.py*" -or
        $_.CommandLine -like "*scripts\updater.ps1*")) })
foreach ($p in $procs) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Say "stopped process $($p.ProcessId) ($($p.Name))" } catch {}
}
Start-Sleep -Seconds 2

Get-NetFirewallRule -Group "CCI Plant Monitoring" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
Say "firewall rules removed"

$dirs = @([Environment]::GetFolderPath("CommonDesktopDirectory"), [Environment]::GetFolderPath("CommonPrograms"),
          [Environment]::GetFolderPath("Desktop"), (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))
foreach ($d in $dirs) {
    $l = Join-Path $d "CCI Plant Monitoring.lnk"
    if ($d -and (Test-Path $l)) { Remove-Item $l -Force; Say "removed $l" }
}

# the window's own browser data in each user's profile (each user owns that folder: never followed if it is a link)
foreach ($u in @(Get-ChildItem "C:\Users" -Directory -Force -ErrorAction SilentlyContinue)) {
    if ($u.Attributes -band $Link) { continue }
    $d = Join-Path $u.FullName "AppData\Local\CCI Plant Monitoring"
    if (Test-Path -LiteralPath $d) { Remove-Tree $d }
}

if ($DeleteData) {
    if (Test-Path $Pd) { Remove-Tree $Pd }
    if (Test-Path $Pd) { Say "note: some of $Pd could not be deleted (in use)" } else { Say "deleted the settings and the collected data ($Pd)" }
} else {
    Say "kept the settings and the collected data in $Pd (a new Setup carries on with them)"
}
exit 0
