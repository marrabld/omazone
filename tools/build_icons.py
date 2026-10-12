"""Render the Omazone application icon into every format the platforms need.

The two SVGs in ``branding/`` are the source of truth. Everything this writes is
generated and committed, deliberately: Qt's SVG rasteriser is not guaranteed to
produce identical pixels across platform Qt builds, so generating per build run
risks the Windows, macOS and Linux packages shipping different icons. One set of
committed bytes keeps them identical.

Outputs land in ``branding/icons/``:

- ``omazone-<size>.png`` for Windows and the Linux hicolor theme
- ``omazone.ico`` for the Windows executable
- ``Omazone.iconset/`` for ``iconutil`` on the macOS runner

Regenerate with ``uv run python tools/build_icons.py``. It needs no dependency
the project does not already ship: QtSvg comes with PySide6-Essentials.
"""

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtSvg  # noqa: E402  (platform set above)

BRANDING = Path(__file__).resolve().parent.parent / "branding"
OUTPUT = BRANDING / "icons"

# Sizes any desktop expects. Below 48 the simplified mark reads better; above it
# the full waveform keeps its shape and its detail.
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
SMALL_SIZES = (16, 24, 32)
# The mark is used by the window at every size it is drawn, so the runtime copy
# is the largest sensible PNG rather than a 16px taskbar icon.
RUNTIME_ICON = "omazone-256.png"

# iconutil expects an iconset directory with these names, some of which are
# @2x variants of a smaller logical size.
ICNS_ENTRIES = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(source: Path, size: int) -> bytes:
    """Rasterise one SVG at one size and return PNG bytes."""
    renderer = QtSvg.QSvgRenderer(str(source))
    if not renderer.isValid():
        raise RuntimeError(f"Cannot read {source}")
    image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    renderer.render(painter)
    painter.end()
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Cannot encode {source} at {size}px")
    return bytes(buffer.data())


def mark_for(size: int) -> Path:
    name = "omazone-small.svg" if size in SMALL_SIZES else "omazone.svg"
    return BRANDING / name


def write_ico(entries: list[tuple[int, bytes]], path: Path):
    """Write a PNG-compressed .ico. The width byte is 0 for a 256px image."""
    out = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    offset = 6 + 16 * len(entries)
    for size, data in entries:
        out += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        offset += len(data)
    for _, data in entries:
        out += data
    path.write_bytes(bytes(out))


def main():
    # Kept referenced for the whole run: Qt images need a live application.
    _app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    OUTPUT.mkdir(parents=True, exist_ok=True)

    written = []
    for size in SIZES:
        data = render(mark_for(size), size)
        (OUTPUT / f"omazone-{size}.png").write_bytes(data)
        written.append(f"omazone-{size}.png")

    write_ico([(size, render(mark_for(size), size)) for size in ICO_SIZES], OUTPUT / "omazone.ico")
    written.append("omazone.ico")

    iconset = OUTPUT / "Omazone.iconset"
    iconset.mkdir(exist_ok=True)
    rendered = {}
    for size in SIZES:
        rendered[size] = render(mark_for(size), size)
    for name, size in ICNS_ENTRIES:
        data = rendered.get(size) or render(mark_for(size), size)
        (iconset / name).write_bytes(data)
        written.append(f"Omazone.iconset/{name}")

    print(f"Wrote {len(written)} files to {OUTPUT}")
    print("macOS: iconutil -c icns Omazone.iconset -o Omazone.icns")


if __name__ == "__main__":
    sys.exit(main())
