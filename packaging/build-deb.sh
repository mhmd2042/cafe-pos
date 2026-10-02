#!/usr/bin/env bash
#
# build-deb.sh — build a Debian (.deb) installer for Bunney POS.
#
#   ./packaging/build-deb.sh
#
# Produces dist/bunney-pos_1.0.0_amd64.deb, installable with:
#   sudo apt install ./dist/bunney-pos_1.0.0_amd64.deb
#
# Layout it installs:
#   /opt/bunney-pos/            the application bundle (root-owned, read-only)
#   /usr/bin/bunney-pos         launcher on PATH
#   /usr/share/applications/    menu entry
#   /usr/share/icons/hicolor/   desktop icon at several sizes
#
# The app detects that /opt is not writable and keeps its database, backups and
# logs in ~/.local/share/bunney-pos instead — see config._default_data_dir().

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' config.py | head -1)"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
PKG="bunney-pos"
BUNDLE="$ROOT/dist/bunney-pos-linux"
STAGE="$ROOT/build/deb/${PKG}_${VERSION}_${ARCH}"
OUT="$ROOT/dist/${PKG}_${VERSION}_${ARCH}.deb"

if [ ! -x "$BUNDLE/bunney-pos" ]; then
    echo "Bundle not found at $BUNDLE" >&2
    echo "Build it first:  ./build-linux.sh" >&2
    exit 1
fi

echo "Building ${PKG} ${VERSION} (${ARCH})…"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/opt/${PKG}" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/doc/${PKG}" \
         "$STAGE/usr/share/icons/hicolor"

# --- application files ----------------------------------------------------- #
cp -a "$BUNDLE/." "$STAGE/opt/${PKG}/"
# The bundle's own launcher and any portable data folder are not wanted here.
rm -f "$STAGE/opt/${PKG}/run-bunney-pos.sh"
rm -rf "$STAGE/opt/${PKG}/logs" "$STAGE/opt/${PKG}/backups" \
       "$STAGE/opt/${PKG}/assets/database"

# --- launcher -------------------------------------------------------------- #
cat > "$STAGE/usr/bin/${PKG}" <<'LAUNCHER'
#!/bin/sh
# Bunney POS launcher. Keeps the Qt platform selection and the UTF-8 locale
# (Qt needs one to shape Arabic) out of the user's way.
APP_DIR="/opt/bunney-pos"
BIN="$APP_DIR/bunney-pos"

if [ ! -x "$BIN" ]; then
    echo "Bunney POS: $BIN is missing. Try reinstalling the package." >&2
    exit 1
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

exec "$BIN" "$@"
LAUNCHER
chmod 0755 "$STAGE/usr/bin/${PKG}"

# --- menu entry ------------------------------------------------------------ #
cp "$HERE/bunney-pos.desktop" "$STAGE/usr/share/applications/${PKG}.desktop"
chmod 0644 "$STAGE/usr/share/applications/${PKG}.desktop"

# --- icons ----------------------------------------------------------------- #
# A desktop icon is mandatory for a menu entry to look right, so it is generated
# at the standard sizes from the source SVG. rsvg-convert or ImageMagick would
# both do; Python + Qt is used here because Qt is already a build dependency and
# renders the SVG exactly as the app itself does.
"$ROOT/.venv/bin/python" - "$ROOT/assets/icons/logo.svg" "$STAGE/usr/share/icons/hicolor" <<'PYICON'
import sys
from pathlib import Path
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

svg, out_root = Path(sys.argv[1]), Path(sys.argv[2])
app = QApplication([])
icon = QIcon(str(svg))
if icon.isNull():
    print("WARNING: could not load the SVG; skipping icons", file=sys.stderr)
    sys.exit(0)

for size in (16, 24, 32, 48, 64, 128, 256, 512):
    directory = out_root / f"{size}x{size}" / "apps"
    directory.mkdir(parents=True, exist_ok=True)
    pixmap = icon.pixmap(QSize(size, size))
    pixmap.save(str(directory / "bunney-pos.png"), "PNG")
print("icons written")
PYICON

# --- documentation --------------------------------------------------------- #
cp "$ROOT/LICENSE" "$STAGE/usr/share/doc/${PKG}/copyright"
gzip -9n -c "$ROOT/README.md" > "$STAGE/usr/share/doc/${PKG}/README.md.gz"

# --- control metadata ------------------------------------------------------ #
INSTALLED_SIZE="$(du -sk "$STAGE" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: ${PKG}
Version: ${VERSION}
Section: misc
Priority: optional
Architecture: ${ARCH}
Maintainer: Bunney POS <noreply@example.com>
Installed-Size: ${INSTALLED_SIZE}
Depends: libc6 (>= 2.35)
Recommends: libgl1, libegl1, libxkbcommon-x11-0, libdbus-1-3
Description: Offline cafe point-of-sale and accounting
 Bunney POS is a local, offline point-of-sale application for a small cafe.
 It covers fast touch ordering, shift management with Z-reports, menu and
 inventory management, sales reporting, and verified backups to an external
 drive. Receipts print on ESC/POS thermal printers.
 .
 It runs entirely on the local machine: no cloud, no API server and no
 internet connection are required.
CONTROL

# Post-install: refresh the desktop and icon caches so the menu entry appears
# immediately instead of after the next login.
cat > "$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ -x "$(command -v update-desktop-database)" ]; then
    update-desktop-database -q /usr/share/applications || true
fi
if [ -x "$(command -v gtk-update-icon-cache)" ]; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
POSTINST

cat > "$STAGE/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ -x "$(command -v update-desktop-database)" ]; then
    update-desktop-database -q /usr/share/applications || true
fi
if [ -x "$(command -v gtk-update-icon-cache)" ]; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
# Note: a user's data lives in ~/.local/share/bunney-pos and is deliberately
# left alone on uninstall. Removing a cafe's sales history on package removal
# would be unforgivable; the README explains how to delete it by hand.
exit 0
POSTRM

chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# --- build ----------------------------------------------------------------- #
mkdir -p "$ROOT/dist"
rm -f "$OUT"

# md5sums let dpkg verify the install.
( cd "$STAGE" && find . -path ./DEBIAN -prune -o -type f -print0 \
    | xargs -0 md5sum > DEBIAN/md5sums ) 2>/dev/null || true

# Compression level is a real trade-off, measured on this 185 MB staging tree:
#
#   gzip   162s -> 68 MB      (dpkg-deb -Zgzip)
#   xz     396s -> 53 MB      (dpkg-deb default, multi-threaded)
#
# xz gives a 22% smaller download but takes 2.4x as long to build. gzip is the
# default here because it keeps the rebuild loop short, which matters while the
# app is still changing. For a release you intend to hand to cafes on slow
# connections, the smaller file is probably worth the wait:
#
#   DEB_COMPRESS=xz ./packaging/build-deb.sh
#
# Note that xz runs multi-threaded and its parent process sits at 0% CPU while
# it waits on the compressor child, so it looks stalled if you only watch the
# top-level process. It is not.
DEB_COMPRESS="${DEB_COMPRESS:-gzip}"

dpkg-deb "-Z${DEB_COMPRESS}" --build --root-owner-group "$STAGE" "$OUT" >/dev/null

# --- verify ---------------------------------------------------------------- #
# A truncated .deb only fails later, on the user's machine, so check it here.
if [ ! -s "$OUT" ]; then
    echo "ERROR: produced an empty archive" >&2
    exit 1
fi
if ! dpkg-deb --info "$OUT" >/dev/null 2>&1; then
    echo "ERROR: not a valid .deb (control archive unreadable)" >&2
    exit 1
fi
PAYLOAD_COUNT="$(dpkg-deb --contents "$OUT" 2>/dev/null | wc -l)"
if [ "$PAYLOAD_COUNT" -lt 100 ]; then
    echo "ERROR: only $PAYLOAD_COUNT payload entries — the archive is truncated" >&2
    exit 1
fi
echo "verified: $PAYLOAD_COUNT files inside the archive"

echo
echo "Built: $OUT"
du -h "$OUT" | cut -f1 | sed 's/^/  size: /'
echo
echo "Install with:"
echo "  sudo apt install \"$OUT\""
echo
echo "Uninstall with:"
echo "  sudo apt remove bunney-pos"
