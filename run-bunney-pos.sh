#!/usr/bin/env bash
#
# run-bunney-pos.sh — launch the packaged Bunney POS bundle (Linux).
#
# Works from the build folder (dist/bunney-pos-linux/) or after copying that
# whole folder anywhere, including a USB stick.
#
#   ./run-bunney-pos.sh
#
# Environment overrides (all optional):
#   BUNNEY_DATA_DIR=/path   where the database, backups and logs are written
#                           (default: the folder holding this script)
#   BUNNEY_DB=/path/file.db use a specific database file
#   BUNNEY_LOGO=/path.svg   use a different logo
#   QT_QPA_PLATFORM=xcb     force X11 instead of Wayland

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/bunney-pos"

if [ ! -x "$BIN" ]; then
    echo "Bunney POS: executable not found at $BIN" >&2
    echo "Make sure this script sits next to the 'bunney-pos' binary." >&2
    exit 1
fi

# Where writable state lives. Defaults to this folder so the whole thing stays
# portable on a USB stick; the app creates it on first run.
export BUNNEY_DATA_DIR="${BUNNEY_DATA_DIR:-$HERE}"

# Prefer Wayland when the session offers it, otherwise fall back to X11.
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
else
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
fi

# Qt needs a UTF-8 locale to shape Arabic correctly. If the user's environment
# reports C/POSIX (common when launched from a desktop file or a minimal shell),
# Qt silently falls back to C.UTF-8 and prints a warning; setting it here keeps
# the output clean and guarantees correct text rendering.
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *UTF-8|*utf8|*UTF8) ;;
    *) export LC_ALL="${LC_ALL:-C.UTF-8}" ;;
esac

exec "$BIN" "$@"
