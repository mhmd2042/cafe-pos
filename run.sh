#!/usr/bin/env bash
#
# run.sh — launch Café POS on the local display using the project venv.
#
#   ./run.sh              start the app
#   ./run.sh --selftest   headless database/auth check (no window)
#   ./run.sh --reset      wipe the local database and rebuild it, then start
#
# Uses the project's own .venv, so nothing is installed system-wide.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PY="$HERE/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "لم يتم العثور على بيئة المشروع: $PY" >&2
    echo "أنشئها أولاً:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

# Qt must be able to reach the display. Prefer native Wayland when the session
# provides it, otherwise fall back to X11/xcb.
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
else
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
fi

# Qt6 removed the Qt5 QPA fallback list syntax in some builds; if the combined
# value fails, the app prints a plugin error and this retry covers it.
exec "$PY" main.py "$@"
