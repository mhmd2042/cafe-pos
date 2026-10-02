#!/usr/bin/env python3
"""
make_icons.py — generate the Windows .ico (and a PNG set) from logo.svg.

    python tools/make_icons.py

Why this exists
---------------
Windows needs a real multi-resolution .ico for the Explorer icon, the taskbar and
the installer's SetupIconFile. Relying on ImageMagick being installed means the
build fails on a clean machine, so this uses QtSvg — which is already a
dependency — and needs nothing extra.

Outputs
    assets/icons/logo.ico          multi-size icon, 16 → 256 px
    assets/icons/png/logo-<N>.png  the same sizes as PNGs (Linux menu icons)

The .ico is committed to the repository so a Windows build works from a fresh
clone without running this script first.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Must be set before QGuiApplication exists; this script renders offscreen.
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QBuffer, QByteArray, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QGuiApplication, QImage, QImageWriter, QPainter  # noqa: E402
from PyQt6.QtSvg import QSvgRenderer  # noqa: E402

SVG_PATH = ROOT / "assets" / "icons" / "logo.svg"
ICO_PATH = ROOT / "assets" / "icons" / "logo.ico"
PNG_DIR = ROOT / "assets" / "icons" / "png"

# Windows picks the closest match from these. 16 and 32 matter most (Explorer,
# taskbar); 256 is used by the large-icon view and by the installer.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int, renderer: QSvgRenderer) -> QImage:
    """Rasterise the SVG at `size` px, preserving transparency."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    # The SVG has a 128x128 viewBox; scale it to the target box.
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def image_to_bytes(image: QImage, fmt: str) -> bytes:
    """
    Encode an image to bytes in memory.

    The QByteArray is held in a local variable on purpose. `QBuffer(QByteArray())`
    hands QBuffer a pointer to a temporary that PyQt destroys as soon as the
    constructor returns, and the writer then segfaults on freed memory. Keeping a
    named reference alive for the duration is what makes this safe.
    """
    payload = QByteArray()
    buf = QBuffer(payload)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    try:
        writer = QImageWriter(buf, fmt.encode())
        if not writer.write(image):
            raise RuntimeError(f"تعذّر ترميز الصورة بصيغة {fmt}: {writer.errorString()}")
    finally:
        buf.close()
    return bytes(payload)


def build_ico(renderer: QSvgRenderer) -> bytes:
    """
    Write a real multi-image .ico.

    Qt's ICO writer only stores a single image, which means Windows scales one
    bitmap up and down and the 16px taskbar icon turns to mush. A proper .ico is
    a small container of PNG frames, so build that directly: a 6-byte header, one
    16-byte directory entry per size, then the PNG payloads.
    """
    frames: list[tuple[int, bytes]] = []
    for size in SIZES:
        png = image_to_bytes(render(size, renderer), "PNG")
        frames.append((size, png))

    header = bytearray()
    header += (0).to_bytes(2, "little")        # reserved
    header += (1).to_bytes(2, "little")        # type: 1 = icon
    header += len(frames).to_bytes(2, "little")

    offset = 6 + 16 * len(frames)
    directory = bytearray()
    for size, png in frames:
        directory += bytes([size if size < 256 else 0])   # 256 is stored as 0
        directory += bytes([size if size < 256 else 0])
        directory += bytes([0, 0])                        # palette count, reserved
        directory += (1).to_bytes(2, "little")            # colour planes
        directory += (32).to_bytes(2, "little")           # bits per pixel
        directory += len(png).to_bytes(4, "little")
        directory += offset.to_bytes(4, "little")
        offset += len(png)

    payload = b"".join(png for _, png in frames)
    return bytes(header) + bytes(directory) + payload


def main() -> int:
    if not SVG_PATH.exists():
        print(f"missing: {SVG_PATH}", file=sys.stderr)
        return 1

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        print(f"invalid SVG: {SVG_PATH}", file=sys.stderr)
        return 1

    # --- .ico for Windows -------------------------------------------------- #
    ico = build_ico(renderer)
    ICO_PATH.write_bytes(ico)

    # --- PNGs for Linux menu icons ----------------------------------------- #
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        (PNG_DIR / f"logo-{size}.png").write_bytes(image_to_bytes(render(size, renderer), "PNG"))

    print(f"wrote {ICO_PATH.relative_to(ROOT)}  ({len(ico):,} bytes, "
          f"{len(SIZES)} sizes: {', '.join(str(s) for s in SIZES)})")
    print(f"wrote {PNG_DIR.relative_to(ROOT)}/logo-*.png  ({len(SIZES)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
