# Offline packages for the plant PC (Windows, Python 3.12 or 3.13, 64-bit)

The site's internet does not reach the Python package server. `scripts/run.ps1` installs everything from this
folder first (no internet needed) and only goes online if something is still missing. Refresh after a change of
requirements.txt with:

    pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 -d wheels -r requirements.txt
    pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 --no-deps -d wheels colorama

(test-only packages - pytest, httpx, scapy - are left out on purpose).

Python 3.13 (the version winget installs today): the four packages with compiled parts are here for 3.13 too
(cffi, charset_normalizer, pydantic_core, pyyaml, `-cp313-` in the name). The install picks the wheels of the
Python it runs on and leaves the others alone. Refresh them with:

    pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp --no-deps -d wheels \
        cffi==2.1.1 charset_normalizer==3.5.2 pydantic_core==2.50.1 pyyaml==6.0.3
