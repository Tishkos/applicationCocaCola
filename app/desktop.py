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


def app_mode_browser(port):
    """Fallback when the native window cannot start (no WebView2 / .NET on this PC): the dashboard in Edge's
    app mode, a window without tabs or an address bar. Edge is part of Windows 10 and 11."""
    url = dashboard_url(port)
    for exe in (os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Microsoft\Edge\Application\msedge.exe")):
        if os.path.exists(exe):
            subprocess.Popen([exe, "--app=" + url, "--new-window"], creationflags=NO_WINDOW if WINDOWS else 0)
            return 0
    os.startfile(url) if WINDOWS else None    # the default browser, as the last way
    return 0


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

    try:
        import webview  # pywebview; if it is missing this ends with an error and launch.ps1 opens the browser window
    except Exception:
        if not installed:
            raise
        return app_mode_browser(port)          # the Setup's copy has no launch.ps1: open Edge's app window instead

    url = dashboard_url(port)
    up = alive(url)

    window = webview.create_window(
        TITLE, url if up else None, html=None if up else waiting_page(),
        width=1400, height=900, min_size=(960, 600), text_select=True,
    )

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
    webview.start(icon=ICON, private_mode=False, storage_path=store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
