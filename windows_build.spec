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

...then copy dist\\BunneyPOS.exe to the till, or wrap it in the installer with
installer.iss.

Design notes
------------
* ONEFILE = True: one .exe to copy onto a cafe PC, which is what you want for a
  till. It unpacks to a temp folder at launch, so the app writes its database,
  backups and logs NEXT TO THE EXE (config.BASE_DIR), never into that temp
  folder — otherwise the data would vanish on exit. When the exe sits in
  Program Files (i.e. installed), config detects that the directory is not
  writable and falls back to %LOCALAPPDATA%\\bunney-pos automatically.
* console=False: no black terminal window behind the POS.
* The icon is a real multi-size .ico, committed to the repo, so a fresh clone
  builds correctly without running any icon-generation step first. Regenerate it
  with `python tools/make_icons.py` if the logo changes.

Qt pruning
----------
PyInstaller's PyQt6 hook collects every Qt library in the wheel, and the
`excludes` option does NOT filter them (it only affects Python imports). The app
uses QtCore, QtGui, QtWidgets and QtSvg, so the rest is removed from the
collected file list below. This takes the bundle from roughly 300 MB to about
half that. The same pruning is applied in bunney_pos.spec (Linux).
"""

from pathlib import Path

ONEFILE = True

# SPECPATH is the directory containing this spec file — i.e. the project root.
PROJECT_ROOT = Path(SPECPATH).resolve()

# --- icon ------------------------------------------------------------------ #
# A real .ico is required for a proper Explorer/taskbar icon and for Inno Setup.
# tools/make_icons.py generates it from logo.svg using QtSvg, so no external
# image tool is needed. Fall back to the SVG only if the .ico is somehow missing.
_ico = PROJECT_ROOT / "assets" / "icons" / "logo.ico"
_svg = PROJECT_ROOT / "assets" / "icons" / "logo.svg"
if _ico.exists():
    ICON_PATH = str(_ico)
elif _svg.exists():
    ICON_PATH = str(_svg)
else:
    ICON_PATH = None

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
    "PyQt6.QtSvg",          # the window icon is an SVG
    "sqlite3",
    "csv",
    "secrets",
    "hashlib",
    "decimal",
    # Printing on Windows goes through the spooler via ctypes; these are stdlib
    # but listing them keeps the analysis honest if the code is ever refactored.
    "ctypes",
    "ctypes.wintypes",
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
        # Python-level excludes. These stop the import graph pulling packages in;
        # the Qt pruning below handles the Qt binaries.
        "tkinter", "matplotlib", "numpy", "pandas", "PIL", "scipy",
        "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQuick",
        "PyQt6.QtQml", "PyQt6.QtMultimedia", "PyQt6.QtCharts",
        "PyQt6.QtDataVisualization", "PyQt6.QtNetwork", "PyQt6.QtSql",
        "PyQt6.QtTest", "PyQt6.QtBluetooth", "PyQt6.QtDesigner",
        "PyQt6.QtHelp", "PyQt6.QtOpenGL", "PyQt6.QtPdf", "PyQt6.QtPositioning",
        "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtStateMachine",
        "PyQt6.QtTextToSpeech", "PyQt6.QtWebChannel", "PyQt6.QtWebSockets",
        "PyQt6.QtRemoteObjects", "PyQt6.QtScxml", "PyQt6.QtSpatialAudio",
        "PyQt6.Qt3DCore", "PyQt6.QtNetworkAuth",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- Qt pruning ------------------------------------------------------------ #
# Matched against the file NAME of every collected binary/DLL. Windows Qt files
# are named like Qt6Quick.dll, so the same substrings work as on Linux.
UNUSED_QT = (
    "Qt6Quick", "Qt6Qml", "Qt6WebEngine", "Qt6WebChannel", "Qt6WebSockets",
    "Qt6Multimedia", "Qt6SpatialAudio", "Qt6TextToSpeech", "Qt6Concurrent",
    "Qt6Charts", "Qt6DataVisualization", "Qt6Graphs", "Qt6Pdf", "Qt6Help",
    "Qt6Designer", "Qt6UiTools", "Qt6Sensors", "Qt6Positioning", "Qt6Location",
    "Qt6Nfc", "Qt6RemoteObjects", "Qt6SerialPort", "Qt6SerialBus",
    "Qt6Bluetooth", "Qt6Test", "Qt6Sql", "Qt6NetworkAuth", "Qt6Network",
    "Qt6OpenGL", "Qt6Vulkan", "Qt6Scxml", "Qt6StateMachine",
    "Qt6VirtualKeyboard", "Qt6Quick3D", "Qt6ShaderTools", "Qt6HttpServer",
    "Qt6EglFS", "Qt6LabsSettings", "Qt6FFmpegStub",
    # Media stack pulled in by the multimedia plugins.
    "avcodec", "avformat", "avutil", "swscale", "swresample",
)

# Qt plugin subdirectories that belong to the pruned modules.
UNUSED_PLUGIN_DIRS = (
    "qmltooling", "qmllint", "qmlls", "qmlformat", "scxmldatamodel",
    "sceneparsers", "geometryloaders", "assetimporters", "renderers",
    "renderplugins", "multimedia", "texttospeech", "sensors", "position",
    "networkinformation", "webview", "designer", "help", "sqldrivers", "tls",
)


def _keep(entry) -> bool:
    """True when a collected binary/datas entry should stay in the bundle."""
    source, dest = str(entry[0]), str(entry[1])
    name = Path(source).name
    full = f"{source}/{dest}".replace("\\", "/")

    if any(token in name for token in UNUSED_QT):
        return False
    if any(f"/{d}/" in full or full.endswith(f"/{d}") for d in UNUSED_PLUGIN_DIRS):
        return False
    return True


_before = len(a.binaries) + len(a.datas)
a.binaries = TOC([e for e in a.binaries if _keep(e)])
a.datas = TOC([e for e in a.datas if _keep(e)])
_after = len(a.binaries) + len(a.datas)
print(f"[spec] pruned {_before - _after} unused Qt files ({_before} -> {_after})")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
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
        version=str(PROJECT_ROOT / "packaging" / "version_info.txt")
        if (PROJECT_ROOT / "packaging" / "version_info.txt").exists() else None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="BunneyPOS",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON_PATH,
        version=str(PROJECT_ROOT / "packaging" / "version_info.txt")
        if (PROJECT_ROOT / "packaging" / "version_info.txt").exists() else None,
    )

    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="BunneyPOS",
    )

# Redirect output to dist/windows/ so Windows and Linux builds never mix.
import PyInstaller.config
PyInstaller.config.CONF['distpath'] = str(PROJECT_ROOT / "dist" / "windows")
