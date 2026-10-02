#!/usr/bin/env bash
#
# build-linux.sh — build the standalone Linux bundle for Bunney POS.
#
#   ./build-linux.sh
#
# Produces dist/bunney-pos-linux/ containing the application, the Qt runtime,
# the SQLite schema and the UI assets. The target machine needs no Python.
#
# The result is NOT portable across different glibc versions: a build made on
# glibc 2.39 will refuse to start on an older distribution. Build on the oldest
# distro you intend to support (or in a container) if you need wide coverage.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PY="$HERE/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "No virtual environment found at $HERE/.venv" >&2
    echo "Create it first:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pyinstaller" >&2
    exit 1
fi

if ! "$PY" -c "import PyInstaller" 2>/dev/null; then
    echo "PyInstaller is not installed in the venv. Installing…" >&2
    "$PY" -m pip install pyinstaller
fi

echo "Building Bunney POS for Linux…"
"$PY" -m PyInstaller --clean --noconfirm bunney_pos.spec

# Ship the launcher inside the bundle so the folder is self-describing.
cp -f run-bunney-pos.sh dist/bunney-pos-linux/
chmod +x dist/bunney-pos-linux/run-bunney-pos.sh dist/bunney-pos-linux/bunney-pos

echo
echo "Done: dist/bunney-pos-linux"
du -sh dist/bunney-pos-linux
echo
echo "Run it with:"
echo "  ./dist/bunney-pos-linux/run-bunney-pos.sh"
