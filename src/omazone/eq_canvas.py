"""Interactive EQ response and playback-following spectrum in one plot."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore

from .manual_eq import frequency_response

COLORS = ("#63dfc0", "#eabb6b", "#c99bff", "#73a8ff", "#ff9086", "#83cfef")


def display_spectrum(spectrum):
    """Auto-scale spectral density for the background; left axis remains EQ dB."""
    spacing = spectrum.frequency[1] - spectrum.frequency[0]
    density = spectrum.power / max(np.sum(spectrum.power) * spacing, 1e-30)
    db = 10 * np.log10(np.maximum(density[1:], 1e-15))
    ceiling = np.percentile(db, 98)
    return spectrum.frequency[1:], np.clip((db - ceiling) * 0.5 + 8, -12, 12)


class BandHandle(pg.TargetItem):
    clicked = QtCore.Signal(int)
    width_changed = QtCore.Signal(int, float)

    def __init__(self, index, color):
        super().__init__(
            pos=(np.log10(2400), 0),
            size=17,
            symbol="o",
            pen=pg.mkPen("#ffffff", width=2),
            brush=pg.mkBrush(color),
            hoverPen=pg.mkPen("#ffffff", width=3),
            hoverBrush=pg.mkBrush(color),
            label=" ",
            labelOpts={
                "color": "#ffffff",
                "offset": (0, -25),
                "fill": pg.mkBrush("#1c2737"),
                "border": pg.mkPen("#384b64"),
            },
        )
        self.index = index
        self.q = 1.0
        self.label().hide()

    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton:
            ev.accept()
            self.clicked.emit(self.index)
        else:
            super().mouseClickEvent(ev)

    def wheelEvent(self, ev):
        steps = ev.delta() / 120
        self.width_changed.emit(self.index, float(np.clip(self.q * 1.1**steps, 0.1, 20)))
        ev.accept()

    def mouseDragEvent(self, ev):
        super().mouseDragEvent(ev)
        self.label().setVisible(self.moving or self.mouseHovering)

    def hoverEvent(self, ev):
        super().hoverEvent(ev)
        self.label().setVisible(not ev.isExit())


class EQCanvas(pg.PlotWidget):
    moved = QtCore.Signal(int, float, float)
    committed = QtCore.Signal(int)
    selected = QtCore.Signal(int)
    width_changed = QtCore.Signal(int, float)
    placed = QtCore.Signal(float, float)

    def __init__(self, axis_type):
        super().__init__(axisItems={"bottom": axis_type(orientation="bottom")})
        self.setLogMode(x=True)
        self.setLabel("bottom", "Frequency", units="Hz")
        self.setLabel("left", "EQ gain", units="dB")
        self.getAxis("bottom").enableAutoSIPrefix(False)
        self.setXRange(np.log10(20), np.log10(20000), padding=0)
        self.getViewBox().setLimits(xMin=np.log10(20), xMax=np.log10(20000), yMin=-20, yMax=20)
        self.getViewBox().setMouseEnabled(x=False, y=False)
        self.setYRange(-12, 12, padding=0)
        self.showGrid(x=True, y=True, alpha=0.12)
        self.plot([20, 20000], [0, 0], pen=pg.mkPen("#647085", style=QtCore.Qt.PenStyle.DashLine))
        self.input_curve = self.plot(
            pen=pg.mkPen("#73a8ff", width=1), fillLevel=-12, brush=pg.mkBrush(115, 168, 255, 35)
        )
        self.output_curve = self.plot(pen=pg.mkPen("#eabb6b", width=1))
        self.response = self.plot(pen=pg.mkPen("#63dfc0", width=3))
        self.individual = self.plot(
            pen=pg.mkPen("#ffffff", width=1, style=QtCore.Qt.PenStyle.DashLine)
        )
        self.handles = []
        self.rate = 48000
        self.updating = False
        self.scene().sigMouseClicked.connect(self.plot_clicked)
        self.setToolTip(
            "Click to add a bell band. Drag a dot for frequency and gain; scroll over it for width."
        )
        self.setTitle("Manual EQ | click to add a band")

    def plot_clicked(self, event):
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and not event.isAccepted()
            and self.getViewBox().sceneBoundingRect().contains(event.scenePos())
        ):
            point = self.getViewBox().mapSceneToView(event.scenePos())
            self.placed.emit(
                float(np.clip(10 ** point.x(), 20, min(20000, self.rate / 2 - 1))),
                float(np.clip(point.y(), -18, 18)),
            )

    def handle_moved(self, handle):
        if not self.updating:
            frequency = self.clamp_frequency(10 ** handle.pos().x())
            gain = float(np.clip(handle.pos().y(), -18, 18))
            self.moved.emit(handle.index, frequency, gain)

    def clamp_frequency(self, value):
        return float(np.clip(value, 20, min(20000, self.rate / 2 - 1)))

    def set_settings(self, settings, rate, selected=0, bypassed=False, scope="Whole recording"):
        self.rate = rate
        high = min(20000, rate / 2 - 1)
        self.getViewBox().setLimits(xMax=np.log10(high))
        self.setXRange(np.log10(20), np.log10(high), padding=0)
        self.updating = True
        try:
            while len(self.handles) > len(settings.bands):
                self.removeItem(self.handles.pop())
            while len(self.handles) < len(settings.bands):
                index = len(self.handles)
                handle = BandHandle(index, COLORS[index % len(COLORS)])
                handle.sigPositionChanged.connect(self.handle_moved)
                handle.sigPositionChangeFinished.connect(
                    lambda item: self.committed.emit(item.index)
                )
                handle.clicked.connect(self.selected)
                handle.width_changed.connect(self.width_changed)
                self.addItem(handle)
                self.handles.append(handle)
            for index, (handle, band) in enumerate(zip(self.handles, settings.bands, strict=True)):
                handle.index = index
                handle.q = band.q
                handle.setPos(np.log10(self.clamp_frequency(band.frequency)), band.gain_db)
                handle.label().setText(
                    f"{band.frequency:g} Hz  {band.gain_db:+.1f} dB  Q {band.q:.2f}"
                )
                handle.label().setVisible(handle.mouseHovering)
                handle.setOpacity(1.0 if band.enabled and not bypassed else 0.4)
                handle.setPen(
                    pg.mkPen(
                        "#ffffff" if index == selected else COLORS[index % len(COLORS)], width=2
                    )
                )
            freq, db = frequency_response(settings, rate)
            self.response.setData(freq[1:], np.zeros_like(db[1:]) if bypassed else db[1:])
            self.response.setPen(pg.mkPen("#647085" if bypassed else "#63dfc0", width=3))
            if 0 <= selected < len(settings.bands) and not bypassed:
                band = settings.bands[selected]
                _, curve = frequency_response(band, rate)
                self.individual.setData(
                    freq[1:], curve[1:] if band.enabled else np.zeros_like(curve[1:])
                )
                self.individual.setPen(
                    pg.mkPen(
                        COLORS[selected % len(COLORS)], width=1, style=QtCore.Qt.PenStyle.DashLine
                    )
                )
            else:
                self.individual.clear()
            maximum = max((abs(b.gain_db) for b in settings.bands), default=0)
            scale = min(19, max(12, maximum + 2))
            self.setYRange(-scale, scale, padding=0)
            self.setTitle(f"Manual EQ | {scope}" + (" | bypassed" if bypassed else ""))
        finally:
            self.updating = False

    def set_spectra(self, before=None, after=None, previous=None):
        if before is None:
            self.input_curve.clear()
        else:
            frequency, values = display_spectrum(before)
            if previous is not None and len(previous) == len(values):
                values = 0.35 * values + 0.65 * previous
            self.input_curve.setData(frequency, values)
        if after is None:
            self.output_curve.clear()
        else:
            self.output_curve.setData(*display_spectrum(after))
