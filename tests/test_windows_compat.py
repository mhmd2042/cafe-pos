"""
tests/test_windows_compat.py — cross-platform readiness checks.

Runs on Linux but exercises the code paths a Windows build depends on, so a
regression shows up here rather than on the café's till. Nothing here needs a
real Windows machine:

  * the writable-data fallback that an installed copy relies on
  * config's platform helpers (drive roots, separate-device detection)
  * the ESC/POS raster builder, verified by round-tripping the bytes a printer
    would receive back into a bitmap and comparing it to the source image
  * that no module calls os.startfile / xdg-open directly instead of going
    through config
  * that the packaged build files reference files that actually exist

The raster round-trip is the important one: it is the only way to prove a
receipt would print correctly without owning a thermal printer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

import config  # noqa: E402
from database.db_manager import DatabaseManager  # noqa: E402
from services.printer_service import PrinterService, ReceiptData  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

_APP = QGuiApplication.instance() or QGuiApplication([])


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    (PASSED if condition else FAILED).append(name)


# --------------------------------------------------------------------------- #
# config: platform helpers
# --------------------------------------------------------------------------- #


def test_platform_flags() -> None:
    print("\n-- platform flags --")
    check("IS_WINDOWS is a bool", isinstance(config.IS_WINDOWS, bool))
    check("IS_LINUX is a bool", isinstance(config.IS_LINUX, bool))
    check("exactly one of linux/windows/macos is true here",
          sum([config.IS_LINUX, config.IS_WINDOWS, config.IS_MACOS]) == 1,
          f"linux={config.IS_LINUX} win={config.IS_WINDOWS} mac={config.IS_MACOS}")


def test_drive_roots() -> None:
    print("\n-- drive discovery --")
    roots = config.drive_roots()
    check("drive_roots() returns a list", isinstance(roots, list))
    check("every root is a Path", all(isinstance(r, Path) for r in roots))
    check("every root exists", all(r.exists() for r in roots),
          f"{len(roots)} root(s): {[str(r) for r in roots]}")


def test_separate_device() -> None:
    print("\n-- external-device detection --")
    here = config.BASE_DIR
    check("the app's own directory is not a separate device",
          config.is_on_separate_device(here) is False)
    check("a nonexistent path is not a separate device",
          config.is_on_separate_device(Path("/nonexistent/xyz")) is False)
    check("returns a bool, never raises",
          isinstance(config.is_on_separate_device(Path("/")), bool))


def test_writable_fallback() -> None:
    print("\n-- writable data fallback (installed copies) --")
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ro = Path(tmp) / "app"
        ro.mkdir()
        ro.chmod(0o500)  # read-only, like /opt or Program Files
        # A fresh interpreter must pick the per-user directory instead.
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "import config;"
            "print(config.APP_DATA_DIR)"
        ) % ROOT
        env = dict(os.environ, BUNNEY_DATA_DIR="", HOME=tmp)
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, cwd=str(ro), env=env, timeout=60)
        out = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
        check("a read-only base dir falls back to a per-user location",
              bool(out) and str(ro) not in out,
              f"chose: {out or result.stderr.strip()[:80]}")
        ro.chmod(0o700)


# --------------------------------------------------------------------------- #
# ESC/POS raster
# --------------------------------------------------------------------------- #


def _decode_escpos(data: bytes, width: int) -> tuple[int, int, list[list[bool]]]:
    """Decode GS v 0 the way a printer would: MSB-first, set bit = ink."""
    assert data[0:3] == bytes([0x1D, 0x76, 0x30]), "missing GS v 0"
    assert data[3] == 0, "unexpected raster mode"
    bpr = data[4] | (data[5] << 8)
    height = data[6] | (data[7] << 8)
    body = data[8:8 + bpr * height]
    assert len(body) == bpr * height, f"body truncated: {len(body)}"
    rows: list[list[bool]] = []
    for r in range(height):
        bits: list[bool] = []
        for byte in body[r * bpr:(r + 1) * bpr]:
            for shift in range(7, -1, -1):
                bits.append(bool((byte >> shift) & 1))
        rows.append(bits[:width])
    return bpr, height, rows


def test_raster_pattern() -> None:
    print("\n-- ESC/POS raster: known pattern round-trip --")
    w, h = 24, 8
    gray = QImage(w, h, QImage.Format.Format_Grayscale8)
    gray.fill(QColor("white"))
    painter = QPainter(gray)
    painter.fillRect(0, 0, w // 3, h, QColor("black"))            # left third
    painter.fillRect(w // 3, 0, w // 3, h // 2, QColor("black"))  # middle, top
    painter.end()
    img = gray.convertToFormat(QImage.Format.Format_Mono,
                               Qt.ImageConversionFlag.MonoOnly)
    path = Path("/tmp/_wp_raster_pattern.png")
    img.save(str(path))

    data = PrinterService._image_to_escpos_raster(path)
    bpr, height, rows = _decode_escpos(data, w)

    check("header declares the right row width", bpr == (w + 7) // 8,
          f"{bpr} bytes/row")
    check("header declares the right height", height == h, f"{height} rows")
    check("job ends with a cut command",
          data.endswith(bytes([0x1D, 0x56, 0x42, 0x00])))

    bad = sum(
        1
        for y in range(h)
        for x in range(w)
        if rows[y][x] != (img.pixelColor(x, y).lightness() < 128)
    )
    check("decoded bitmap is pixel-identical to the source", bad == 0,
          f"{bad} differing pixels")


def test_raster_real_receipt() -> None:
    print("\n-- ESC/POS raster: real Arabic receipt --")
    db_path = Path("/tmp/_wp_receipt.db")
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    db = DatabaseManager(db_path)
    service = PrinterService(db)

    receipt = ReceiptData(
        cafe_name="مقهى الاختبار",
        order_number="WP-1",
        created_at="2026-10-02 12:00:00",
        lines=[{"name": "لاتيه", "quantity": 2, "unit_price_minor": 1500,
                "line_total_minor": 3000, "modifiers": ["كبير"], "note": ""}],
        total_minor=3000,
        footer="شكراً لزيارتكم",
    )
    image_path = service.render_image(receipt)
    rendered = QImage(str(image_path))
    job = PrinterService._image_to_escpos_raster(image_path)
    bpr, height, rows = _decode_escpos(job, rendered.width())

    check("job geometry matches the rendered image",
          bpr == (rendered.width() + 7) // 8 and height == rendered.height(),
          f"{rendered.width()}x{rendered.height()}")

    bad = sum(
        1
        for y in range(height)
        for x in range(rendered.width())
        if rows[y][x] != (rendered.pixelColor(x, y).lightness() < 128)
    )
    check("every pixel survives the ESC/POS conversion", bad == 0,
          f"{bad} differing pixels")

    ink = sum(sum(row) for row in rows)
    pct = ink / (bpr * height * 8) * 100
    check("ink coverage is plausible (not blank, not solid)", 1 < pct < 60,
          f"{pct:.1f}%")

    rows_with_ink = sum(1 for row in rows if any(row))
    check("text appears on many rows", rows_with_ink > 5,
          f"{rows_with_ink} of {height} rows")

    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# source hygiene
# --------------------------------------------------------------------------- #


def test_no_direct_platform_launchers() -> None:
    print("\n-- no platform-specific launchers outside config --")
    import ast

    offenders: list[str] = []
    for py in ROOT.rglob("*.py"):
        if any(part in {".venv", "build", "dist", "__pycache__", "tests"}
               for part in py.parts):
            continue
        if py.name == "config.py":
            # config is where these calls are *supposed* to live.
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        # Walk the AST so only real call sites count. A docstring that mentions
        # os.startfile to explain why it is avoided must not trip this.
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "startfile":
                offenders.append(f"{py.relative_to(ROOT)}:{node.lineno} os.startfile")
            if isinstance(node, ast.Constant) and node.value == "xdg-open":
                offenders.append(f"{py.relative_to(ROOT)}:{node.lineno} xdg-open")

    check("all shell/file-manager launches go through config", not offenders,
          "; ".join(offenders) or "none found")


def test_build_files_reference_existing_paths() -> None:
    print("\n-- build files reference real files --")
    check("windows_build.spec exists", (ROOT / "windows_build.spec").exists())
    check("bunney_pos.spec exists", (ROOT / "bunney_pos.spec").exists())
    check("installer.iss exists", (ROOT / "installer.iss").exists())

    ico = ROOT / "assets" / "icons" / "logo.ico"
    check("a real .ico exists for the Windows build", ico.exists(),
          f"{ico.stat().st_size:,} bytes" if ico.exists() else "missing")

    if ico.exists():
        import struct

        blob = ico.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", blob[:6])
        check("the .ico is a valid icon container",
              reserved == 0 and kind == 1 and count >= 4,
              f"{count} sizes")

        sizes = []
        for i in range(count):
            entry = blob[6 + 16 * i: 6 + 16 * (i + 1)]
            w = entry[0] or 256
            offset = struct.unpack("<I", entry[12:16])[0]
            payload = blob[offset:offset + 4]
            sizes.append((w, payload == b"\x89PNG"))
        check("every .ico frame is a real PNG payload",
              all(ok for _, ok in sizes),
              ", ".join(f"{w}px" for w, _ in sizes))
        check("the .ico includes the 16px taskbar size",
              any(w == 16 for w, _ in sizes))
        check("the .ico includes a 256px Explorer size",
              any(w == 256 for w, _ in sizes))

    check("installer.iss guards its optional icon copy",
          "skipifsourcedoesntexist" in (ROOT / "installer.iss").read_text(encoding="utf-8"))


def test_no_posix_only_imports() -> None:
    print("\n-- no POSIX-only imports --")
    banned = ("fcntl", "termios", "pwd", "grp", "resource", "pty", "syslog")
    offenders: list[str] = []
    for py in ROOT.rglob("*.py"):
        if any(part in {".venv", "build", "dist", "__pycache__"} for part in py.parts):
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                for mod in banned:
                    if stripped in (f"import {mod}", f"from {mod} import") \
                            or stripped.startswith(f"import {mod} ") \
                            or stripped.startswith(f"from {mod}."):
                        offenders.append(f"{py.relative_to(ROOT)}: {stripped}")
    check("no module imports a Unix-only library", not offenders,
          "; ".join(offenders) or "none found")


def main() -> int:
    print("=" * 70)
    print(f" Windows-compatibility test — {config.APP_NAME} v{config.VERSION}")
    print("=" * 70)

    test_platform_flags()
    test_drive_roots()
    test_separate_device()
    test_writable_fallback()
    test_raster_pattern()
    test_raster_real_receipt()
    test_no_direct_platform_launchers()
    test_build_files_reference_existing_paths()
    test_no_posix_only_imports()

    print()
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", ", ".join(FAILED))
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
