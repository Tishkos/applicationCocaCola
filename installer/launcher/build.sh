#!/usr/bin/env bash
# Builds the two program exes into the folder given (needs mingw-w64: apt install gcc-mingw-w64-x86-64).
#   installer/launcher/build.sh <out dir> <version 1.2.3>
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$1"; ver="$2"
IFS=. read -r a b c <<< "$ver"
mkdir -p "$out"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
build() {   # $1 exe name, $2 description, $3 extra cflags
    local name="$1" desc="$2" flags="${3:-}"
    (cd "$here" && x86_64-w64-mingw32-windres -DVER_A="$a" -DVER_B="$b" -DVER_C="$c" -DVER_STR="\\\"$ver\\\"" \
        -DDESC="\\\"$desc\\\"" -DNAME="\\\"$name\\\"" launcher.rc -O coff -o "$tmp/res.o")
    x86_64-w64-mingw32-gcc -O2 -s -Wall -Wextra -Werror -municode -mwindows $flags -o "$out/$name.exe" "$here/launcher.c" "$tmp/res.o" -lshell32
}
build "CCI Plant Monitoring" "CCI Plant Monitoring"
build "CCI Plant Monitoring Service" "CCI Plant Monitoring background service" "-DSERVICE"
ls -la "$out"/*.exe
