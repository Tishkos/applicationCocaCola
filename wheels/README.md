# Offline packages for the plant PC (Windows, Python 3.12, 64-bit)

The site's internet does not reach the Python package server. `scripts/run.ps1` installs everything from this
folder first (no internet needed) and only goes online if something is still missing. Refresh after a change of
requirements.txt with:

    pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 -d wheels -r requirements.txt
    pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 --no-deps -d wheels colorama

(test-only packages - pytest, httpx, scapy - are left out on purpose).
