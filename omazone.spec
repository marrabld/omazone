# PyInstaller spec for the Omazone desktop workbench.
# Build: uv run pyinstaller --noconfirm --distpath dist omazone.spec
#
# Onedir (not onefile): faster startup and fewer antivirus false positives.
# The GitHub Actions workflow zips dist/Omazone for release upload.

import subprocess
import sys
import tomllib
from pathlib import Path


with open("pyproject.toml", "rb") as metadata:
    app_version = tomllib.load(metadata)["project"]["version"]

# One icon for every platform, built from branding/ by tools/build_icons.py.
# The .icns is produced by iconutil, which only exists on the macOS runner, so
# each build makes the format it needs from the committed PNGs.
ICON_PNG = "branding/icons/omazone-256.png"
ICON_ICO = "branding/icons/omazone.ico"
ICNS = "dist/Omazone.icns"
ICONSET = "branding/icons/Omazone.iconset"

if sys.platform == "darwin" and not Path(ICNS).exists():
    subprocess.run(["iconutil", "-c", "icns", ICONSET, "-o", ICNS], check=True)

a = Analysis(
    ["src/omazone/__main__.py"],
    pathex=["src"],
    binaries=[],
    # Carried inside the package so the window icon resolves in the bundle.
    datas=[(ICON_PNG, "omazone")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Full PySide6 is not installed (we depend on PySide6-Essentials); keep
    # unused toolkits out of the bundle in case they appear via other imports.
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Omazone",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Omazone",
    icon=None if sys.platform == "darwin" else ICON_ICO,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Omazone.app",
        bundle_identifier="au.com.omazone.app",
        version=app_version,
        icon=ICNS,
        info_plist={
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            "CFBundleIconFile": "Omazone.icns",
        },
    )
