r"""Plant Collector as a real desktop window.

A native window (pywebview on top of Edge WebView2, which every Windows 10/11 has) with the
title "Plant Collector" and the collector's own icon - in the title bar and in the taskbar.
No address bar, no tabs, no "localhost" anywhere on the screen. If the collector is not running
it is started first (real plant, read-only, the same as START-PLANT.bat) and the window shows the
dashboard as soon as it answers.

One window only: a second start (the icon clicked again while the window is open or still
opening) brings the existing window to the front and ends at once. The taskbar knows the window
as "CCI.PlantCollector" (its AppUserModelID; the shortcut carries the same name), so it shows this
icon and not Python's, and "Pin to taskbar" pins the Plant Collector shortcut.

Started by app\launch.ps1 (the desktop shortcut). By hand:
    .venv\Scripts\pythonw.exe app\desktop.py            real plant
    .venv\Scripts\pythonw.exe app\desktop.py --demo     demo plant
    .venv\Scripts\pythonw.exe app\desktop.py --no-start never start the collector, only open the window
Installed by the Setup, the shortcut runs it with --installed --data <ProgramData>\CCI Plant Monitoring\data:
the collector is then the background service (Windows task PlantDataCollector) and is only ever started
as that task, never from this folder.
Needs the pywebview package (in requirements.txt and the wheels folder); without it, launch.ps1
falls back to an Edge/Chrome app-mode window.
"""
import ctypes
import base64
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, "app", "PlantCollector.ico")
LOGO = os.path.join(ROOT, "app", "PlantCollector.png")      # the same logo, 256 px, for the start-up page
TITLE = "Plant Collector"
APP_ID = "CCI.PlantCollector"                 # AppUserModelID - the same string as in Install-DesktopShortcut.ps1
MUTEX = "Local\\CCI.PlantCollector.window"    # one window per Windows session
TASK = "PlantDataCollector"                   # the Windows task that scripts\run.ps1 registers
WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000                        # CREATE_NO_WINDOW: helper commands without a console flashing up

WAITING_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Plant Collector</title>
<style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;font-family:Segoe UI,Arial,sans-serif;
background:radial-gradient(ellipse at 50% 38%,#2a1116 0,#121318 55%,#0c0d11 100%);color:#e5e7eb}.b{text-align:center}
.logo{width:256px;height:256px;display:block;margin:0 auto 26px;filter:drop-shadow(0 18px 36px rgba(214,24,36,.38))}
h1{font-weight:600;font-size:22px;margin:0 0 8px;letter-spacing:.01em}p{color:#9aa3b2;margin:0;font-size:14px}
.s{display:inline-block;width:14px;height:14px;border-radius:50%;border:2px solid #4b5563;border-top-color:#e5484d;
animation:r 1s linear infinite;vertical-align:-2px;margin-right:8px}@keyframes r{to{transform:rotate(360deg)}}</style></head>
<body><div class="b">LOGO<h1>Plant Collector</h1><p><span class="s"></span>Starting the collector…</p></div></body></html>"""


def waiting_page(text="Starting the collector…"):
    """The start-up page: the logo at 256 px, the window's name and one line of text."""
    try:
        with open(LOGO, "rb") as fh:
            logo = '<img class="logo" src="data:image/png;base64,%s" alt="">' % base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        logo = ""
    return (WAITING_HTML.replace("LOGO", logo, 1).replace("Starting the collector…", text)
            .replace("Plant Collector", TITLE))


DATA = os.path.join(ROOT, "data")             # --data: where the collector writes dashboard.url (ProgramData when installed)


def dashboard_url(port):
    url = "http://localhost:%d/" % port
    try:
        with open(os.path.join(DATA, "dashboard.url"), encoding="utf-8") as fh:
            line = fh.readline().strip()
        if line:
            url = line if line.endswith("/") else line + "/"
    except OSError:
        pass
    return url


def alive(url):
    try:
        urllib.request.urlopen(url + "api/v1/health", timeout=2)
        return True
    except Exception:
        return False


# ---- Windows: taskbar identity, one instance, icon -------------------------------------------------
def set_app_id():
    """Tell Windows which application this window belongs to. Without it the taskbar files the window
    under pythonw.exe and shows Python's icon."""
    if WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass


_mutex = None   # kept for the life of the process; Windows drops it when the process ends (no stale lock file)


def single_instance():
    """True when no other Plant Collector window exists. Otherwise that window is brought to the front
    and the caller ends."""
    global _mutex
    if not WINDOWS:
        return True
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = ctypes.c_void_p
        k.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        _mutex = k.CreateMutexW(None, 0, MUTEX)
        if ctypes.get_last_error() == 183:    # ERROR_ALREADY_EXISTS
            focus_existing()
            return False
    except Exception:
        pass
    return True


def focus_existing():
    try:
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, TITLE)
        if hwnd:
            if u.IsIconic(hwnd):
                u.ShowWindow(hwnd, 9)         # SW_RESTORE
            u.SetForegroundWindow(hwnd)       # refused by Windows at times - then the taskbar button flashes
    except Exception:
        pass


def set_icon(window):
    """pywebview gives the form the icon when it is created (icon= in webview.start). The taskbar
    button is created when the window is shown; tell it once more through Win32, so it never keeps
    the icon of pythonw.exe."""
    if not WINDOWS:
        return
    try:
        u = ctypes.windll.user32
        u.LoadImageW.restype = ctypes.c_void_p
        u.LoadImageW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        u.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
        try:
            hwnd = int(window.native.Handle.ToInt64())
        except Exception:
            hwnd = u.FindWindowW(None, TITLE)
        if not hwnd:
            return
        for which, size in ((0, u.GetSystemMetrics(49)), (1, u.GetSystemMetrics(11))):   # ICON_SMALL, ICON_BIG
            h = u.LoadImageW(None, ICON, 1, size, size, 0x10)       # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                u.SendMessageW(hwnd, 0x0080, which, h)              # WM_SETICON
    except Exception:
        pass


# ---- starting the collector -----------------------------------------------------------------------
def start_task(port, demo):
    """Fast path: the Windows task is already registered for this plant and port - just start it
    (a few seconds, no administrator prompt when Windows allows it). False when it is not registered
    like that or Windows refuses; the caller then goes the long way through scripts\\run.ps1."""
    if not WINDOWS:
        return False
    try:
        q = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/XML"], capture_output=True, timeout=15,
                           creationflags=NO_WINDOW)
        raw = q.stdout or b""
        xml = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:64] else "utf-8", "replace")
        want = "site.yaml" if demo else "site.plant.yaml"
        if q.returncode != 0 or want not in xml or (" --port %d " % port) not in xml:
            return False
        r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, timeout=15, creationflags=NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def start_task_any():
    """Start the background service task as it is registered (the Setup's copy). Harmless when it runs."""
    if not WINDOWS:
        return False
    try:
        r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, timeout=15, creationflags=NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def start_collector(port, demo):
    """Same as START-PLANT.bat / START.bat without the browser tab: scripts\\run.ps1 registers the
    Windows task (asks for administrator rights once) or runs in a window if that is refused."""
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Minimized", "-File",
            os.path.join(ROOT, "scripts", "run.ps1"), "-NoBrowser", "-Port", str(port)]
    if not demo:
        args.append("-Plant")
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(args, cwd=ROOT, creationflags=flags)


WEBVIEW2_KEYS = ("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",   # Microsoft Edge WebView2 Runtime (and the preview channels)
                 "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}", "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",
                 "{65C35B14-6C1D-4122-AC46-7148CC9D6497}")


def webview2_ready():
    """The same test pywebview makes before it chooses Edge WebView2: .NET Framework 4.6.2 or newer and a WebView2
    Runtime of version 86 or newer. Without them pywebview falls back to Internet Explorer's engine, which cannot
    show this dashboard (the window would stay blank)."""
    if not WINDOWS:
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as k:
            if winreg.QueryValueEx(k, "Release")[0] < 394802:
                return False
        for key in WEBVIEW2_KEYS:
            for hive, path in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}"),
                               (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}"),
                               (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}")):
                try:
                    with winreg.OpenKey(hive, path) as k:
                        pv = str(winreg.QueryValueEx(k, "pv")[0])
                    if int(pv.split(".")[0]) >= 86:
                        return True
                except (OSError, ValueError):
                    pass
    except Exception:
        return False
    return False


def message(text, ok_cancel=False):
    """A plain Windows message box (the window cannot show anything itself here). True for OK."""
    if not WINDOWS:
        return True
    try:
        flags = (0x01 if ok_cancel else 0x00) | 0x40            # MB_OKCANCEL | MB_ICONINFORMATION
        return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags) == 1
    except Exception:
        return True


def app_mode_browser(port):
    """Fallback when the native window cannot start: the dashboard in Edge's or Chrome's app mode (a window
    without tabs or an address bar), else in the default browser."""
    url = dashboard_url(port)
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    local = os.environ.get("LOCALAPPDATA", "")
    for exe in (os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"), os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(pf, r"Google\Chrome\Application\chrome.exe"), os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(local, r"Google\Chrome\Application\chrome.exe") if local else ""):
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, "--app=" + url, "--new-window"], creationflags=NO_WINDOW if WINDOWS else 0)
            return 0
    if WINDOWS:
        os.startfile(url)                     # the default browser, as the last way
    return 0


NO_WEBVIEW2 = ("This PC has no Microsoft Edge WebView2 Runtime, which the CCI Plant Monitoring window needs.\n\n"
               "The background service and Turkey's feed are not affected: they keep running.\n\n"
               "To get the window: install the free 'Microsoft Edge WebView2 Runtime' (Evergreen) from Microsoft, "
               "then open CCI Plant Monitoring again.\n\nClick OK to open the dashboard in a browser now.")


# what the page's calls act on: kept here, outside the object handed to the page (pywebview resolves dotted names
# on that object, so nothing but its two methods should be reachable from the page)
_WIN = {"window": None, "installed": False, "full": False}
_WIN_LOCK = threading.Lock()


class WindowApi:
    """What the dashboard page may ask of this window (window.pywebview.api.* in the page): two methods."""

    def fullscreen(self, on=None):
        """Fill the whole screen (on=True), back to a normal window (False), or switch (None). Returns the state.
        The browser's own full screen does not work inside this window, so the page asks for it here. One call
        at a time (each page call runs on its own thread)."""
        with _WIN_LOCK:
            w = _WIN["window"]
            want = (not _WIN["full"]) if on is None else bool(on)
            if w is not None and want != _WIN["full"]:
                w.toggle_fullscreen()
                _WIN["full"] = want
            return _WIN["full"]

    def start_update(self, version=""):
        """'Install now' was clicked: show the update window (it follows the download and the installation and
        opens the program again when it is done), then close this window so none of its files is in use. False
        (the window stays) when the update window could not start, e.g. a policy that blocks PowerShell scripts."""
        if not (_WIN["installed"] and WINDOWS):
            return False
        script = os.path.join(ROOT, "scripts", "update-progress.ps1")     # PowerShell reads it whole at the start
        if not os.path.exists(script):
            return False
        try:
            p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-STA",
                                  "-File", script, "-InstallDir", ROOT, "-Version", str(version or "")],
                                 creationflags=NO_WINDOW, close_fds=True)
        except Exception:
            return False
        time.sleep(2.5)
        if p.poll() is not None:              # ended at once: no update window; the page shows its message instead
            return False
        w = _WIN["window"]
        if w is not None:
            threading.Timer(0.5, w.destroy).start()
        return True


def main():
    global DATA, TITLE
    demo = "--demo" in sys.argv
    no_start = "--no-start" in sys.argv
    installed = "--installed" in sys.argv        # the Setup's copy: the background service is the only way to start it
    if installed:
        TITLE = "CCI Plant Monitoring"            # the Setup's name for the application (Start Menu, Apps list, window)
    port = 8080
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        if a == "--data" and i + 1 < len(sys.argv):
            DATA = sys.argv[i + 1]

    set_app_id()                              # before any window exists
    if not single_instance():
        return 0                              # the open window has the focus now

    if installed and not webview2_ready():
        if message(NO_WEBVIEW2, ok_cancel=True):
            return app_mode_browser(port)
        return 0
    try:
        import webview  # pywebview; if it is missing this ends with an error and launch.ps1 opens the browser window
    except Exception:
        if not installed:
            raise
        message("The CCI Plant Monitoring window could not start on this PC. The dashboard opens in a browser instead.")
        return app_mode_browser(port)          # the Setup's copy has no launch.ps1

    url = dashboard_url(port)
    up = alive(url)

    _WIN["installed"] = installed
    window = webview.create_window(
        TITLE, url if up else None, html=None if up else waiting_page(),
        width=1400, height=900, min_size=(960, 600), text_select=True, js_api=WindowApi(),
    )
    _WIN["window"] = window

    def wait_and_load():
        """Runs beside the window: starts the collector if needed, shows the dashboard when it answers."""
        if up:
            return
        deadline = time.time() + 180          # the first start installs packages: give it time
        retry_at = None
        if installed:
            if not no_start:
                start_task_any()              # the background service: start it if Windows had it stopped
        elif not no_start:
            if start_task(port, demo):
                retry_at = time.time() + 30   # the task was started; if it does not answer, the long way
            else:
                start_collector(port, demo)
        while time.time() < deadline:
            time.sleep(1)
            u = dashboard_url(port)
            if alive(u):
                window.load_url(u)
                return
            if retry_at and time.time() > retry_at:
                retry_at = None
                start_collector(port, demo)
        window.load_html(waiting_page("The background service did not answer. Restart the PC, or run the Setup again to repair it."
                                      if installed else "The collector did not answer. Run RUN-IN-WINDOW.bat to see why."))

    window.events.shown += lambda: set_icon(window)
    threading.Thread(target=wait_and_load, daemon=True).start()
    # the window's own browser data: the user's profile when installed (ProgramData belongs to the service)
    store = os.path.join(os.environ.get("LOCALAPPDATA") or DATA, "CCI Plant Monitoring", "appwindow") if installed \
        else os.path.join(DATA, "appwindow")
    try:
        webview.start(icon=ICON, private_mode=False, storage_path=store)
    except Exception:
        if not installed:
            raise
        message("The CCI Plant Monitoring window could not start on this PC (.NET or WebView2 problem). "
                "The dashboard opens in a browser instead; the background service is not affected.")
        return app_mode_browser(port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
