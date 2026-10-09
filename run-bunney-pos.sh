#!/usr/bin/env bash
#
# run-bunney-pos.sh — launch the packaged Bunney POS bundle (Linux).
#
# Works from the build folder (dist/linux/) or after copying that
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

# Pick a Qt platform plugin that will actually work on this machine.
#
# Qt 6.5+ needs libxcb-cursor0 for the X11 ("xcb") plugin. It is not installed
# everywhere, and when it is missing Qt aborts with "no Qt platform plugin could
# be initialized" — a confusing failure for a user who just wants the till to
# open. A "wayland;xcb" fallback list does not help: Qt tries each in turn and
# still aborts if the first one cannot initialise.
#
# So probe instead of guess: prefer Wayland when the session offers it, and only
# fall back to X11 when its cursor library is actually present.
if [ -z "${QT_QPA_PLATFORM:-}" ]; then
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then
        export QT_QPA_PLATFORM="wayland"
    elif ldconfig -p 2>/dev/null | grep -q "libxcb-cursor\.so"; then
        export QT_QPA_PLATFORM="xcb"
    else
        # Last resort: let Qt decide, and say why if it cannot.
        echo "Bunney POS: no Wayland session and libxcb-cursor0 is missing." >&2
        echo "  Install it for X11 support:  sudo apt install libxcb-cursor0" >&2
        echo "  Or force a backend:          QT_QPA_PLATFORM=wayland ./run-bunney-pos.sh" >&2
    fi
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
