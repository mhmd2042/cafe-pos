# -*- mode: python ; coding: utf-8 -*-
"""
bunney_pos.spec — PyInstaller build spec for Bunney POS (Linux).

Produces a self-contained bundle in dist/linux/ containing the
application, its Qt runtime, the SQLite schema and the UI assets. The target
machine needs no Python and no virtual environment.

Build:
    ./build-linux.sh
    # or directly:
    .venv/bin/pyinstaller --clean --noconfirm bunney_pos.spec

Why this spec prunes Qt
-----------------------
PyInstaller's PyQt6 hook collects *every* Qt shared library that ships in the
wheel — Quick, QML, WebEngine, Multimedia, 3D, Charts and friends. Listing them
in `excludes` does nothing: that option filters Python imports, not the Qt
binaries the hook copies.

The app uses only QtCore, QtGui, QtWidgets and QtSvg, so the unused libraries
are removed from the collected file list below. That takes the bundle from
~330 MB to roughly a third of that, which also stops the .deb step from taking
a quarter of an hour (compressing 330 MB of redundant Qt is slow).

Anything pruned here would cause an ImportError at launch, so the list is
deliberately conservative: only modules the app never touches are removed.
"""

import sys
from pathlib import Path

ONEFILE = False

block_cipher = None

# SPECPATH is the directory containing this spec file — i.e. the project root.
# Do not take .parent of it, or the datas paths resolve one level too high.
PROJECT_ROOT = Path(SPECPATH).resolve()

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
]

from PyInstaller.utils.hooks import collect_dynamic_libs  # noqa: E402

binaries = []
try:
    binaries += collect_dynamic_libs("PyQt6")
except Exception:  # pragma: no cover - hook unavailable
    pass

# --- Qt pruning ------------------------------------------------------------ #
# Substrings matched against the *file name* of every collected binary. Anything
# matching is dropped. Keep this list in sync with what the app actually uses:
# QtCore, QtGui, QtWidgets, QtSvg (+ the platform/imageformat plugins).
UNUSED_QT = (
    "Qt6Quick", "Qt6Qml", "Qt6QuickControls2", "Qt6QuickTemplates2",
    "Qt6QuickLayouts", "Qt6QuickShapes", "Qt6QuickEffects", "Qt6QuickWidgets",
    "Qt6WebEngine", "Qt6WebChannel", "Qt6WebSockets", "Qt6WebView",
    "Qt6Multimedia", "Qt6SpatialAudio", "Qt6TextToSpeech",
    "Qt6QmlModels", "Qt6QmlWorkerScript", "Qt6QmlMeta", "Qt6QmlLocalStorage",
    "Qt6QmlXmlListModel", "Qt6QuickTest", "Qt6QuickTimeline",
    "Qt6QuickDialogs", "Qt6QuickParticles", "Qt6QuickVectorImage",
    "Qt6Quick3D", "Qt6ShaderTools", "Qt6Concurrent",
    "Qt6Charts", "Qt6DataVisualization", "Qt6Graphs",
    "Qt6Pdf", "Qt6PdfQuick", "Qt6PdfWidgets",
    "Qt6Help", "Qt6Designer", "Qt6UiTools",
    "Qt6Sensors", "Qt6Positioning", "Qt6Location", "Qt6Nfc",
    "Qt6RemoteObjects", "Qt6SerialPort", "Qt6SerialBus", "Qt6Bluetooth",
    "Qt6Test", "Qt6Sql", "Qt6NetworkAuth", "Qt6HttpServer",
    "Qt6OpenGLWidgets", "Qt6OpenGL", "Qt6EglFS", "Qt6Vulkan",
    # Qt6WlShellIntegration is NOT pruned: it provides the wl-shell compositor
    # integration the Wayland plugin looks for at startup. Qt6WaylandCompositor
    # (for *being* a compositor) is safe to drop.
    "Qt6WaylandCompositor",
    "Qt6Scxml", "Qt6StateMachine", "Qt6VirtualKeyboard", "Qt6LabSettings",
    "Qt6HttpServer", "Qt6Network",
    # Media / codec stack pulled in by the multimedia plugins
    "libavcodec", "libavformat", "libavutil", "libswscale", "libswresample",
    "libQt6FFmpegStub",
    # ICU data duplicated at the top level; Qt's own copy is kept inside PyQt6/
    "libicudata.so.74", "libicuuc.so.74", "libicui18n.so.74",
)

# Qt plugin *directories* that belong to the pruned modules. Removing the whole
# directory is cleaner than listing every plugin file.
UNUSED_PLUGIN_DIRS = (
    "qmltooling", "qmllint", "qmlls", "qmlformat", "scxmldatamodel",
    "sceneparsers", "geometryloaders", "assetimporters", "renderers",
    "renderplugins", "multimedia", "texttospeech", "sensors",
    "position", "networkinformation", "webview", "designer", "help",
    "sqldrivers", "tls", "egldeviceintegrations",
    # NOTE: "wayland-shell-integration" and "wayland-decoration-client" are
    # deliberately NOT pruned. They hold libxdg-shell.so, which is what actually
    # creates a Wayland window; without it the app aborts at startup with
    # "No shell integration named xdg-shell found" on every Wayland desktop.
    # They look like optional compositor extras but are load-bearing here.
)


def _keep(entry) -> bool:
    """True when a collected binary/datas entry should stay in the bundle."""
    # entries are (source, dest_dir) or (source, dest_dir, type)
    source, dest = str(entry[0]), str(entry[1])
    name = Path(source).name
    full = f"{source}/{dest}"

    if any(token in name for token in UNUSED_QT):
        return False
    if any(f"/{d}/" in full or full.endswith(f"/{d}") for d in UNUSED_PLUGIN_DIRS):
        return False
    return True


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
        # Python-level excludes. These stop the import graph from pulling the
        # packages in at all; the Qt pruning above handles the Qt binaries.
        "tkinter", "matplotlib", "numpy", "pandas", "PIL", "scipy",
        "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQuick",
        "PyQt6.QtQml", "PyQt6.QtMultimedia", "PyQt6.QtCharts",
        "PyQt6.QtDataVisualization", "PyQt6.QtNetwork", "PyQt6.QtSql",
        "PyQt6.QtTest", "PyQt6.QtBluetooth", "PyQt6.QtDesigner",
        "PyQt6.QtHelp", "PyQt6.QtOpenGL", "PyQt6.QtPdf", "PyQt6.QtPositioning",
        "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtStateMachine",
        "PyQt6.QtTextToSpeech", "PyQt6.QtWebChannel", "PyQt6.QtWebSockets",
        "PyQt6.QtRemoteObjects", "PyQt6.QtScxml", "PyQt6.QtSpatialAudio",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- apply the pruning ------------------------------------------------------ #
_before = len(a.binaries) + len(a.datas)
a.binaries = TOC([e for e in a.binaries if _keep(e)])
a.datas = TOC([e for e in a.datas if _keep(e)])
_after = len(a.binaries) + len(a.datas)
print(f"[spec] pruned {_before - _after} unused Qt files "
      f"({_before} -> {_after})")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name="bunney-pos",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(PROJECT_ROOT / "assets" / "icons" / "logo.svg"),
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
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
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="bunney-pos-linux",
    )
