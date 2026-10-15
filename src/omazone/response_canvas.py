"""Shared static response axes and playback-following spectrum curves."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore


def display_spectrum(spectrum):
    """Auto-scale spectral density for the background; left axis remains EQ dB."""
    spacing = spectrum.frequency[1] - spectrum.frequency[0]
    density = spectrum.power / max(np.sum(spectrum.power) * spacing, 1e-30)
    db = 10 * np.log10(np.maximum(density[1:], 1e-15))
    ceiling = np.percentile(db, 98)
    return spectrum.frequency[1:], np.clip((db - ceiling) * 0.5 + 8, -12, 12)


class ResponseCanvas(pg.PlotWidget):
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
