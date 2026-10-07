# PyInstaller spec for the Omazone desktop workbench.
# Build: uv run pyinstaller --noconfirm --distpath dist omazone.spec
#
# Onedir (not onefile): faster startup and fewer antivirus false positives.
# The GitHub Actions workflow zips dist/Omazone for release upload.

import sys
import tomllib


with open("pyproject.toml", "rb") as metadata:
    app_version = tomllib.load(metadata)["project"]["version"]

a = Analysis(
    ["src/omazone/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
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
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Omazone.app",
        bundle_identifier="au.com.omazone.app",
        version=app_version,
        info_plist={"LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True},
    )
