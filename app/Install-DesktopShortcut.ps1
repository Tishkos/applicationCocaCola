# Creates the "Plant Collector" shortcut on the Desktop and in the Start Menu, with the
# collector's own icon (app\PlantCollector.ico). The shortcut runs app\launch.ps1, which starts
# the collector if needed and opens the dashboard as an application window.
#
# The shortcut is tagged with the application name "CCI.PlantCollector" (AppUserModelID); the
# window (app\desktop.py) registers itself under the same name. That is what makes the taskbar
# show this icon for the running window instead of Python's, lets "Pin to taskbar" pin the real
# shortcut, and keeps a pinned icon and the running window on one taskbar button.
#
# Run once (INSTALL-APP.bat does it). Safe to run again: the shortcuts are simply rewritten.
# -Remove deletes them again.
#
# The Setup (scripts\install.ps1) uses it with:
#   -AllUsers     the Public Desktop and the all-users Start Menu instead of the current user's
#   -Name         the shortcut's name ("CCI Plant Monitoring")
#   -Target       the program the shortcut runs (.venv\Scripts\pythonw.exe) instead of app\launch.ps1
#   -Arguments    its command line ("app\desktop.py" --installed --data ...)
#   -NoDesktop    only the Start Menu (a Desktop shortcut that is there is removed)
param(
    [switch]$Remove,
    [switch]$AllUsers,
    [string]$Name = "Plant Collector",
    [string]$Target = "",
    [string]$Arguments = "",
    [switch]$NoDesktop
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$name = $Name
$appId = "CCI.PlantCollector"                             # the same string as APP_ID in app\desktop.py
$icon = Join-Path $root "app\PlantCollector.ico"
$launcher = Join-Path $root "app\launch.ps1"
if (-not $Remove) {
    if (-not (Test-Path $icon)) { throw "icon not found: $icon" }
    if (-not $Target -and -not (Test-Path $launcher)) { throw "launcher not found: $launcher" }
    if ($Target -and -not (Test-Path $Target)) { throw "program not found: $Target" }
}

# Writes System.AppUserModel.ID into a .lnk (the shell's IPropertyStore on a ShellLink; WScript.Shell
# cannot do it). Compiled on first use; a failure here only costs the taskbar tag, never the shortcut.
$csAppId = @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace PlantCollector
{
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PropertyKey
    {
        public Guid fmtid;
        public uint pid;
        public PropertyKey(Guid f, uint p) { fmtid = f; pid = p; }
    }

    [StructLayout(LayoutKind.Sequential)]
    public sealed class PropVariant : IDisposable
    {
        public ushort vt;
        public ushort r1;
        public ushort r2;
        public ushort r3;
        public IntPtr p;
        public int p2;
        public PropVariant(string s) { vt = 31; p = Marshal.StringToCoTaskMemUni(s); }   // VT_LPWSTR
        public void Dispose() { PropVariantClear(this); }
        [DllImport("ole32.dll")] private static extern int PropVariantClear([In, Out] PropVariant pv);
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore
    {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, [Out] PropVariant value);
        void SetValue(ref PropertyKey key, [In] PropVariant value);
        void Commit();
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046"), ClassInterface(ClassInterfaceType.None)]
    public class CShellLink { }

    public static class ShortcutAppId
    {
        public static void Set(string path, string appId)
        {
            object link = new CShellLink();
            IPersistFile file = (IPersistFile)link;
            file.Load(path, 0);
            IPropertyStore store = (IPropertyStore)link;
            PropertyKey key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);   // System.AppUserModel.ID
            using (PropVariant v = new PropVariant(appId))
            {
                store.SetValue(ref key, v);
                store.Commit();
            }
            file.Save(path, true);
        }
    }
}
'@
function Set-ShortcutAppId([string]$path, [string]$id) {
    try {
        if (-not ("PlantCollector.ShortcutAppId" -as [type])) { Add-Type -TypeDefinition $csAppId -IgnoreWarnings -ErrorAction Stop | Out-Null }
        [PlantCollector.ShortcutAppId]::Set($path, $id)
        return $true
    } catch {
        Write-Output "[app] (taskbar tag not written: $($_.Exception.Message))" | Out-Host
        return $false
    }
}

if ($AllUsers) {
    $desk = [Environment]::GetFolderPath("CommonDesktopDirectory")
    $menu = [Environment]::GetFolderPath("CommonPrograms")
} else {
    $desk = [Environment]::GetFolderPath("Desktop")
    $menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
}
$places = @(@{ dir = $desk; want = (-not $NoDesktop) }, @{ dir = $menu; want = $true })
$shell = New-Object -ComObject WScript.Shell
foreach ($pl in $places) {
    $dir = $pl.dir
    if (-not $dir -or -not (Test-Path $dir)) { continue }
    $path = Join-Path $dir "$name.lnk"
    if ($Remove -or -not $pl.want) {
        if (Test-Path $path) { Remove-Item $path -Force; Write-Output "[app] removed $path" }
        continue
    }
    $lnk = $shell.CreateShortcut($path)
    if ($Target) {
        $lnk.TargetPath = $Target
        $lnk.Arguments = $Arguments
        $lnk.WindowStyle = 1
    } else {
        $lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
        $lnk.WindowStyle = 7                              # minimized console, hidden by -WindowStyle Hidden anyway
    }
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$icon,0"
    $lnk.Description = "CCI Plant Monitoring - dashboard (read-only)"
    $lnk.Save()
    $tagged = Set-ShortcutAppId $path $appId
    if ($tagged) { Write-Output "[app] shortcut written: $path  (taskbar: $appId)" } else { Write-Output "[app] shortcut written: $path" }
}
if (-not $Remove) {
    # Refresh the icon cache so the new icon shows at once (harmless if it fails). Not as SYSTEM (an update by the
    # updater): there is no desktop to refresh, and the tool may stay behind there.
    if (-not [Security.Principal.WindowsIdentity]::GetCurrent().IsSystem) {
        try { & "$env:SystemRoot\System32\ie4uinit.exe" -show } catch {}
    }
    Write-Output "[app] Done. Double-click '$name' on the Desktop or in the Start Menu."
}
