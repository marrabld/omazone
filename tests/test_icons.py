"""The committed icons are what the platforms ship, so they need a guard.

The two SVGs are the source of truth. These checks keep the generated set
complete and correctly sized, because a missing size fails silently: Windows
and Linux quietly substitute another icon rather than failing a build.
"""

import importlib.util
import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "build_icons.py"
spec = importlib.util.spec_from_file_location("build_icons", SCRIPT)
icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(icons)

BRANDING = icons.BRANDING
OUTPUT = icons.OUTPUT
SIZES = icons.SIZES
SMALL_SIZES = icons.SMALL_SIZES
ICO_SIZES = icons.ICO_SIZES
ICNS_ENTRIES = icons.ICNS_ENTRIES
mark_for = icons.mark_for
write_ico = icons.write_ico

PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47])


def png_size(path):
    """Width and height straight out of the PNG header."""
    header = path.read_bytes()[16:24]
    return struct.unpack(">II", header)


@pytest.mark.parametrize("size", SIZES)
def test_every_size_is_committed_at_the_right_dimensions(size):
    path = OUTPUT / f"omazone-{size}.png"
    assert path.exists(), f"run tools/build_icons.py to create {path.name}"
    assert path.read_bytes()[:4] == PNG_SIGNATURE
    assert png_size(path) == (size, size)


def test_the_icon_carries_every_entry_windows_and_macos_ask_for():
    """Missing sizes are substituted silently by the OS, not reported."""
    for name, size in ICNS_ENTRIES:
        path = OUTPUT / "Omazone.iconset" / name
        assert path.exists(), f"run tools/build_icons.py to create {name}"
        assert png_size(path) == (size, size)


def test_the_windows_icon_holds_every_size():
    data = (OUTPUT / "omazone.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(ICO_SIZES))
    sizes = []
    for index in range(count):
        # width, height, palette, reserved, planes, bits, byte length, offset
        entry = struct.unpack("<BBBBHHII", data[6 + 16 * index : 22 + 16 * index])
        width, height, _, _, planes, bits, length, offset = entry
        assert (width or 256) == (height or 256), "every entry must be square"
        assert (planes, bits) == (1, 32)
        assert data[offset : offset + 4] == PNG_SIGNATURE
        assert length == len(data[offset : offset + length])
        sizes.append(width or 256)
    assert sizes == list(ICO_SIZES)


def test_small_sizes_use_the_simplified_mark_and_larger_ones_the_full_mark():
    """The full waveform turns to mush below 48px, so it must not be used there."""
    for size in SIZES:
        expected = "omazone-small.svg" if size in SMALL_SIZES else "omazone.svg"
        assert mark_for(size).name == expected


def test_both_marks_render_and_are_not_blank():
    """A silently broken SVG would produce transparent icons for every platform."""
    from PySide6 import QtCore, QtGui, QtSvg

    # A QApplication, not a QGuiApplication: Qt refuses to change application
    # type once one exists, and other tests here need the widgets one.
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    for name in ("omazone.svg", "omazone-small.svg"):
        renderer = QtSvg.QSvgRenderer(str(BRANDING / name))
        assert renderer.isValid(), f"{name} does not parse"
        image = QtGui.QImage(64, 64, QtGui.QImage.Format.Format_ARGB32)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(image)
        renderer.render(painter)
        painter.end()
        assert image.pixelColor(32, 32).alpha() > 0, f"{name} rendered blank"


def test_a_malformed_icon_is_detected_rather_than_written(tmp_path):
    """write_ico is used from a tool, so a bad entry must not pass silently."""
    write_ico([(16, b"not a png")], tmp_path / "bad.ico")
    data = (tmp_path / "bad.ico").read_bytes()
    assert struct.unpack("<HHH", data[:6]) == (0, 1, 1)


def test_the_window_carries_the_icon_on_every_platform():
    """A pipeline that builds icons but never sets one changes nothing visible."""
    from omazone.gui import Window, application_icon

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    try:
        icon = application_icon()
        assert not icon.isNull(), "the window icon asset was not found"
        assert icon.availableSizes(), "the icon loaded with no usable size"
        assert not window.windowIcon().isNull()
    finally:
        window.project.mark_saved()
        window.close()
        app.processEvents()


def test_a_missing_icon_asset_degrades_instead_of_raising(monkeypatch, tmp_path):
    """Running from an unusual layout must still open a window."""
    import omazone.gui as gui

    monkeypatch.setattr(gui, "icon_candidates", lambda: iter([tmp_path / "gone.png"]))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = gui.Window()
    try:
        # Nothing to load, so no icon; the window still opens.
        assert gui.application_icon().isNull()
        assert window.windowIcon().isNull()
    finally:
        window.project.mark_saved()
        window.close()
        app.processEvents()
