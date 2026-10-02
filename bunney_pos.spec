# -*- mode: python ; coding: utf-8 -*-
"""
bunney_pos.spec — PyInstaller build spec for Bunney POS (Linux).

Produces a self-contained bundle in dist/bunney-pos-linux/ containing the
application, its Qt runtime, the SQLite schema and the UI assets. The target
machine needs no Python and no virtual environment.

Build:
    .venv/bin/pyinstaller --clean --noconfirm bunney_pos.spec

Design notes
------------
* The app writes its database, backups, receipts and logs *next to the
  executable* (see config.BASE_DIR), not inside the bundle. A onefile build
  extracts to a temp directory that PyInstaller deletes on exit, so writable
  state must never live there. This spec therefore also works with
  ONEFILE = True, but the default is a directory build: it starts faster and
  the user can see and copy their own data folder.
* The Qt platform plugins, image format plugins and TLS/codec bits are pulled
  in by PyInstaller's PyQt6 hooks. Nothing is pruned here — a café till that
  fails to open because a plugin was excluded is far worse than a 90 MB folder.
* assets/ is bundled read-only. The stylesheets and the logo are read at
  runtime through config.BUNDLE_DIR.
"""

import sys
from pathlib import Path

# Build as a directory bundle by default. Set the environment variable to build
# a single file instead (slower start, but one artefact to copy).
ONEFILE = False

block_cipher = None

# SPECPATH is the directory containing this spec file — i.e. the project root.
# Do not take .parent of it, or the datas paths resolve one level too high.
PROJECT_ROOT = Path(SPECPATH).resolve()

# --- what ships inside the bundle ----------------------------------------- #
datas = [
    # SQLite schema + seed data. Read at first launch to create the database.
    (str(PROJECT_ROOT / "database" / "schema.sql"), "database"),
    # Stylesheets (QSS) and icons, including logo.svg.
    (str(PROJECT_ROOT / "assets" / "styles"), "assets/styles"),
    (str(PROJECT_ROOT / "assets" / "icons"), "assets/icons"),
]

# Qt ships its plugins as separate shared libraries; PyInstaller's PyQt6 hook
# normally collects them, but being explicit avoids a class of "could not find
# or load the Qt platform plugin" failures on machines with no Qt installed.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs  # noqa: E402

binaries = []
hiddenimports = [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtSvg",          # the window icon is an SVG
    "sqlite3",
    "csv",
    "secrets",
    "hashlib",
    "decimal",
]

# Belt and braces: make sure the SVG image-format plugin travels with the build,
# otherwise the logo silently fails to load (the loader degrades gracefully, but
# the icon would just be missing).
try:
    binaries += collect_dynamic_libs("PyQt6")
except Exception:  # pragma: no cover - hook not available
    pass

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Keep the bundle lean; none of these are imported by the app.
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtQuick",
        "PyQt6.QtQml",
        "PyQt6.Qt3DCore",
        "PyQt6.QtMultimedia",
        "PyQt6.QtBluetooth",
        "PyQt6.QtNetworkAuth",
        "PyQt6.QtCharts",
        "PyQt6.QtDataVisualization",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="bunney-pos",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,               # UPX breaks Qt plugins on some distros
        runtime_tmpdir=None,
        console=False,           # no terminal window for a GUI app
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(PROJECT_ROOT / "assets" / "icons" / "logo.svg"),
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="bunney-pos",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(PROJECT_ROOT / "assets" / "icons" / "logo.svg"),
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="bunney-pos-linux",
    )
