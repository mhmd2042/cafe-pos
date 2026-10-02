# -*- mode: python ; coding: utf-8 -*-
"""
windows_build.spec — PyInstaller build spec for Bunney POS (Windows).

Run this on a Windows machine; PyInstaller cannot cross-compile, so a Linux box
cannot produce a .exe. The result is dist\\BunneyPOS.exe — a single file with no
console window and no Python installation required on the target PC.

Two-step build on Windows (PowerShell or cmd), from the project root:

    py -m venv .venv
    .venv\\Scripts\\pip install -r requirements.txt pyinstaller
    .venv\\Scripts\\pyinstaller --clean --noconfirm windows_build.spec

...then copy dist\\BunneyPOS.exe to the till.

Design notes
------------
* ONEFILE = True: one .exe to copy onto a café PC, which is what you want for a
  till. It unpacks to a temp folder at launch, so the app writes its database,
  backups and logs NEXT TO THE EXE (config.BASE_DIR), never into that temp
  folder — otherwise the data would vanish on exit.
* console=False: no black terminal window behind the POS.
* The icon is embedded in the .exe. Windows wants a real .ico for this;
  PyInstaller will accept the bundled SVG in some versions but is not reliable
  with it, so if you want a proper Explorer icon convert logo.svg to logo.ico
  (e.g. with ImageMagick: magick logo.svg -define icon:auto-resize=256,128,64,48,32,16 logo.ico)
  and set ICON_PATH below to that file.
"""

import os
from pathlib import Path

ONEFILE = True

# SPECPATH is the directory containing this spec file — i.e. the project root.
PROJECT_ROOT = Path(SPECPATH).resolve()

# --- icon ------------------------------------------------------------------ #
# Prefer a real .ico next to the SVG if one has been generated; fall back to the
# SVG so the build still succeeds without it.
_ico = PROJECT_ROOT / "assets" / "icons" / "logo.ico"
_svg = PROJECT_ROOT / "assets" / "icons" / "logo.svg"
ICON_PATH = str(_ico if _ico.exists() else _svg)

block_cipher = None

# --- bundled read-only resources ------------------------------------------- #
datas = [
    (str(PROJECT_ROOT / "database" / "schema.sql"), "database"),
    (str(PROJECT_ROOT / "assets" / "styles"), "assets/styles"),
    (str(PROJECT_ROOT / "assets" / "icons"), "assets/icons"),
]

hiddenimports = [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtSvg",
    "sqlite3",
    "csv",
    "secrets",
    "hashlib",
    "decimal",
]

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BunneyPOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # UPX frequently corrupts Qt DLLs on Windows
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                # GUI app: no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
    version=None,                 # add a version resource file here if you want
                                  # file properties (company, version) in Explorer
)
