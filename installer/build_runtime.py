"""Builds the standalone program folder that the Setup installs (build/stage): Windows Python + every package +
the program, so the PC needs nothing else.

  python installer/build_runtime.py [version]

  1. the Windows Python runtime: the conda-forge packages in installer/runtime.lock, each checked against its
     SHA-256, unpacked into build/stage/runtime (their DLLs next to python313.dll; tests, IDLE, tkinter, headers
     and the like left out)
  2. the packages from wheels/ (the cp313 Windows wheels and the pure-Python ones) into runtime/Lib/site-packages,
     offline, exactly those files
  3. the program: plantcollector/, profiles/, config/site.plant.yaml, app/ (window), scripts/ (set-up, updater,
     uninstall), requirements.txt, README.md, version.txt
  4. the two exes (installer/launcher: needs mingw-w64)
  5. everything compiled to .pyc (when this Python is 3.13 like the runtime), so the first start is quick even
     where the program folder is read-only
  6. a check that the important files are there

Downloads go to build/cache (kept between builds). Needs: Python 3.10+, pip, zstd (apt install zstd).
"""

from __future__ import annotations

import glob
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
CACHE = os.path.join(BUILD, "cache")
STAGE = os.path.join(BUILD, "stage")
RUNTIME = os.path.join(STAGE, "runtime")
CHANNEL = "https://conda.anaconda.org/conda-forge/win-64/"
PY = "3.13"

# not needed by the program; left out of the runtime
DROP_LIB = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data", "venv",
            "turtle.py", "__phello__"]
DROP_TOP = ["Library", "conda-meta", "info", "include", "libs", "Scripts", "Tools", "share", "etc", "tcl", "Menu",
            "LICENSE_PYTHON.txt"]


def say(m):
    print("[build] " + m, flush=True)


def lock():
    out = []
    with open(os.path.join(ROOT, "installer", "runtime.lock"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                sha, name = line.split()
                out.append((sha.lower(), name))
    return out


def fetch(sha, name):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if not (os.path.exists(path) and hashlib.sha256(open(path, "rb").read()).hexdigest() == sha):
        say(f"downloading {name}")
        with urllib.request.urlopen(CHANNEL + name, timeout=300) as r:
            data = r.read()
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            raise SystemExit(f"SHA-256 of {name} is {got}, the lock says {sha}: not used")
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    return path


def unpack(path, dest):
    """A .conda file: a zip with pkg-*.tar.zst (the files) and info-*.tar.zst (metadata, not needed)."""
    with zipfile.ZipFile(path) as z:
        inner = [n for n in z.namelist() if n.startswith("pkg-") and n.endswith(".tar.zst")]
        if len(inner) != 1:
            raise SystemExit(f"{path}: unexpected layout {z.namelist()}")
        raw = subprocess.run(["zstd", "-d", "-c", "-q"], input=z.read(inner[0]), capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        for m in t.getmembers():
            if m.issym() or m.islnk() or m.name.startswith(("/", "..")) or "/../" in m.name:
                continue                                   # nothing outside the folder, no links (none expected)
            t.extract(m, dest, filter="data") if sys.version_info >= (3, 12) else t.extract(m, dest)


def rm(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def build_runtime():
    rm(RUNTIME)
    os.makedirs(RUNTIME)
    for sha, name in lock():
        unpack(fetch(sha, name), RUNTIME)
    # the libraries' DLLs (Library\bin) next to python313.dll, where Windows finds them for every .pyd
    for dll in glob.glob(os.path.join(RUNTIME, "Library", "bin", "*.dll")):
        shutil.copy2(dll, os.path.join(RUNTIME, os.path.basename(dll)))
    for d in DROP_TOP:
        rm(os.path.join(RUNTIME, d))
    for d in DROP_LIB:
        rm(os.path.join(RUNTIME, "Lib", d))
    for pyd in glob.glob(os.path.join(RUNTIME, "DLLs", "_tkinter*")) + glob.glob(os.path.join(RUNTIME, "tcl*.dll")) + \
            glob.glob(os.path.join(RUNTIME, "tk*.dll")) + glob.glob(os.path.join(RUNTIME, "vcomp*.dll")) + \
            glob.glob(os.path.join(RUNTIME, "**", "*.pdb"), recursive=True) + \
            glob.glob(os.path.join(RUNTIME, "DLLs", "_test*.pyd")) + glob.glob(os.path.join(RUNTIME, "DLLs", "_ctypes_test*.pyd")) + \
            glob.glob(os.path.join(RUNTIME, "DLLs", "xxlimited*.pyd")):
        rm(pyd)                                            # tkinter, OpenMP, debug symbols, CPython's own test modules
    sp = os.path.join(RUNTIME, "Lib", "site-packages")
    rm(sp)
    os.makedirs(sp)
    for p in glob.glob(os.path.join(RUNTIME, "**", "__pycache__"), recursive=True):
        rm(p)
    for need in ("python313.dll", "pythonw.exe", "python.exe", "vcruntime140.dll", os.path.join("Lib", "os.py"),
                 os.path.join("DLLs", "_sqlite3.pyd"), os.path.join("DLLs", "_ctypes.pyd"), os.path.join("DLLs", "_ssl.pyd")):
        if not os.path.exists(os.path.join(RUNTIME, need)):
            raise SystemExit(f"the runtime has no {need}")
    say("Python runtime unpacked")


def install_wheels():
    def wanted(w):
        pytag, abi, plat = os.path.basename(w)[:-4].split("-")[-3:]
        if abi == "none" and plat == "any":
            return True                                    # pure Python (also pythonnet: cp311.cp312.cp313...-none-any)
        return "cp313" in pytag.split(".") and plat == "win_amd64"
    whl = sorted(w for w in glob.glob(os.path.join(ROOT, "wheels", "*.whl")) if wanted(w))
    say(f"installing {len(whl)} wheels into the runtime (offline)")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-deps", "--no-index",
                    "--no-compile", "--target", os.path.join(RUNTIME, "Lib", "site-packages"), "--platform", "win_amd64",
                    "--python-version", PY, "--implementation", "cp", "--abi", "cp313", "--only-binary=:all:", *whl], check=True)
    sp = os.path.join(RUNTIME, "Lib", "site-packages")
    rm(os.path.join(sp, "bin"))
    for need in ("pymodbus", "yaml", "pydantic", "pydantic_core", "fastapi", "starlette", "uvicorn", "openpyxl", "requests",
                 "xlrd", "tzdata", "webview", "pythonnet", "clr_loader", "cffi", "colorama"):
        if not os.path.exists(os.path.join(sp, need)):
            raise SystemExit(f"the package {need} is missing from site-packages")


def copy_program(version):
    for d in ("plantcollector", "profiles"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(STAGE, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "index.classic.html"))
    os.makedirs(os.path.join(STAGE, "config"))
    for f in ("site.plant.yaml", "added-devices.yaml"):     # added-devices: what an update adds to installed settings
        shutil.copy2(os.path.join(ROOT, "config", f), os.path.join(STAGE, "config"))
    os.makedirs(os.path.join(STAGE, "app"))
    for f in ("desktop.py", "PlantCollector.ico", "PlantCollector.png", "Install-DesktopShortcut.ps1"):
        shutil.copy2(os.path.join(ROOT, "app", f), os.path.join(STAGE, "app"))
    os.makedirs(os.path.join(STAGE, "scripts"))
    for f in ("install.ps1", "updater.ps1", "uninstall.ps1", "update-progress.ps1"):
        shutil.copy2(os.path.join(ROOT, "scripts", f), os.path.join(STAGE, "scripts"))
    for f in ("requirements.txt", "README.md"):
        shutil.copy2(os.path.join(ROOT, f), STAGE)
    with open(os.path.join(STAGE, "version.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(version + "\n")


def build_exes(version):
    subprocess.run(["bash", os.path.join(ROOT, "installer", "launcher", "build.sh"), STAGE, version], check=True,
                   stdout=subprocess.DEVNULL)


def compile_all():
    if sys.version_info[:2] != (3, 13):
        say(f"this Python is {sys.version_info[0]}.{sys.version_info[1]}, the runtime 3.13: no .pyc made (the program "
            "compiles itself at the first start)")
        return
    # the runtime never changes in place: unchecked. The program is checked against its source, so a .py edited on
    # site is never shadowed by an old .pyc.
    for d, mode in ((os.path.join(RUNTIME, "Lib"), "unchecked-hash"), (os.path.join(STAGE, "plantcollector"), "checked-hash")):
        subprocess.run([sys.executable, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", mode,
                        "-x", r"[\\/](test|tests|testing)[\\/]|lib2to3", d], check=False, stdout=subprocess.DEVNULL)
    say(".pyc files made")


def main():
    version = (sys.argv[1] if len(sys.argv) > 1 else "").lstrip("v")
    if not version:
        ns = {}
        exec(open(os.path.join(ROOT, "plantcollector", "__init__.py"), encoding="utf-8").read(), ns)
        version = ns["__version__"]
    rm(STAGE)
    os.makedirs(STAGE)
    build_runtime()
    install_wheels()
    copy_program(version)
    build_exes(version)
    compile_all()
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(STAGE) for f in fs)
    say(f"stage ready: {STAGE} ({size / 1e6:.0f} MB, version {version})")


if __name__ == "__main__":
    main()
