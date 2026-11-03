#!/usr/bin/env bash
# Builds the standalone Windows Setup: dist/CCI-Plant-Monitoring-Setup-<version>.exe and dist/SHA256SUMS.txt
# (the updater downloads both from a GitHub Release and checks the SHA-256 before it installs).
#   installer/build.sh            the version in plantcollector/__init__.py
#   installer/build.sh 1.2.3      that version (a release tag v1.2.3 builds 1.2.3)
# Steps: installer/build_runtime.py (build/stage: Windows Python + packages + program + the two exes), then NSIS.
# Needs (Debian/Ubuntu): python3 3.13 with pip, zstd, nsis, gcc-mingw-w64-x86-64.
set -euo pipefail
cd "$(dirname "$0")/.."
version="${1:-}"
if [ -z "$version" ]; then
  version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' plantcollector/__init__.py)"
fi
version="${version#v}"
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "version must look like 1.2.3 (got '$version')" >&2
  exit 1
fi
python="${PYTHON:-python3}"
"$python" installer/build_runtime.py "$version"
mkdir -p dist
rm -f "dist/CCI-Plant-Monitoring-Setup-$version.exe"
makensis -V2 -DVERSION="$version" installer/CCI-Plant-Monitoring.nsi
cd dist
sha256sum "CCI-Plant-Monitoring-Setup-$version.exe" > SHA256SUMS.txt
cat SHA256SUMS.txt
ls -la "CCI-Plant-Monitoring-Setup-$version.exe"
