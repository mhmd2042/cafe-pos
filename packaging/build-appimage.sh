#!/usr/bin/env bash
#
# build-appimage.sh — build an AppImage for Bunney POS.
#
#   ./packaging/build-appimage.sh
#
# Produces dist/BunneyPOS-1.0.0-x86_64.AppImage: a single file the user marks
# executable and runs. No installation, no root, no dependencies beyond glibc.
#
# AppImages mount their payload as a read-only squashfs, so the app cannot write
# beside its own executable. config._default_data_dir() detects that and stores
# data in ~/.local/share/bunney-pos instead.
#
# This script downloads appimagetool on first use (it is not packaged by most
# distributions). Set APPIMAGETOOL to use an existing copy instead.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' config.py | head -1)"
ARCH_LABEL="x86_64"
BUNDLE="$ROOT/dist/linux"
APPDIR="$ROOT/build/appimage/BunneyPOS.AppDir"
OUT="$ROOT/dist/BunneyPOS-${VERSION}-${ARCH_LABEL}.AppImage"
TOOL="${APPIMAGETOOL:-$ROOT/build/appimagetool-${ARCH_LABEL}.AppImage}"

if [ ! -x "$BUNDLE/bunney-pos" ]; then
    echo "Bundle not found at $BUNDLE" >&2
    echo "Build it first:  ./build-linux.sh" >&2
    exit 1
fi

# --- fetch appimagetool if needed ------------------------------------------ #
if [ ! -x "$TOOL" ]; then
    echo "appimagetool not found; downloading…"
    mkdir -p "$(dirname "$TOOL")"
    URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH_LABEL}.AppImage"
    if ! curl -fL --retry 3 -o "$TOOL" "$URL"; then
        echo "Download failed. Install appimagetool yourself and re-run with:" >&2
        echo "  APPIMAGETOOL=/path/to/appimagetool ./packaging/build-appimage.sh" >&2
        exit 1
    fi
    chmod +x "$TOOL"
fi

echo "Building BunneyPOS ${VERSION} AppImage…"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor" "$APPDIR/usr/share/doc/bunney-pos"

# --- payload --------------------------------------------------------------- #
cp -a "$BUNDLE/." "$APPDIR/usr/bin/"
rm -f "$APPDIR/usr/bin/run-bunney-pos.sh"
rm -rf "$APPDIR/usr/bin/logs" "$APPDIR/usr/bin/backups" \
       "$APPDIR/usr/bin/assets/database"

# --- icon ------------------------------------------------------------------ #
"$ROOT/.venv/bin/python" - "$ROOT/assets/icons/logo.svg" "$APPDIR" <<'PYICON'
import sys
from pathlib import Path
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

svg, appdir = Path(sys.argv[1]), Path(sys.argv[2])
app = QApplication([])
icon = QIcon(str(svg))

# AppImage requires .DirIcon and a top-level icon; hicolor entries let the
# desktop environment pick a size that suits the panel.
icon.pixmap(QSize(256, 256)).save(str(appdir / "bunney-pos.png"), "PNG")
icon.pixmap(QSize(256, 256)).save(str(appdir / ".DirIcon"), "PNG")
for size in (16, 24, 32, 48, 64, 128, 256, 512):
    directory = appdir / "usr/share/icons/hicolor" / f"{size}x{size}" / "apps"
    directory.mkdir(parents=True, exist_ok=True)
    icon.pixmap(QSize(size, size)).save(str(directory / "bunney-pos.png"), "PNG")
print("icons written")
PYICON

# --- desktop entry --------------------------------------------------------- #
# The AppImage spec wants the entry at the AppDir root as well as in the usual
# share/applications location.
sed 's|^Exec=.*|Exec=bunney-pos|' "$HERE/bunney-pos.desktop" > "$APPDIR/bunney-pos.desktop"
cp "$APPDIR/bunney-pos.desktop" "$APPDIR/usr/share/applications/bunney-pos.desktop"

# --- AppRun ---------------------------------------------------------------- #
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# Bunney POS AppRun — the entry point the AppImage executes.
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$PATH"

# The bundle is a read-only squashfs mount, so data cannot live beside the
# executable. Point it at the user's data directory (the app would detect this
# anyway; being explicit keeps it out of any stray writable mount).
if [ -z "${BUNNEY_DATA_DIR:-}" ]; then
    XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
    BUNNEY_DATA_DIR="$XDG_DATA_HOME/bunney-pos"
    export BUNNEY_DATA_DIR
fi

if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
else
    QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
fi
export QT_QPA_PLATFORM

case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *UTF-8|*utf8|*UTF8) ;;
    *) LC_ALL="${LC_ALL:-C.UTF-8}"; export LC_ALL ;;
esac

exec "$HERE/usr/bin/bunney-pos" "$@"
APPRUN
chmod 0755 "$APPDIR/AppRun"

cp "$ROOT/LICENSE" "$APPDIR/usr/share/doc/bunney-pos/copyright"

# --- build ----------------------------------------------------------------- #
mkdir -p "$ROOT/dist"
rm -f "$OUT"

# appimagetool refuses to run as root in some environments and wants a clean
# env; ARCH is required when the tool cannot infer it from the filename.
ARCH="$ARCH_LABEL" "$TOOL" --no-appstream "$APPDIR" "$OUT"

echo
echo "Built: $OUT"
du -h "$OUT" | cut -f1 | sed 's/^/  size: /'
echo
echo "Run it with:"
echo "  chmod +x \"$OUT\""
echo "  \"$OUT\""
