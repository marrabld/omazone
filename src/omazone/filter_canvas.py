"""Corrective-filter response with frequency-only markers."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore
from scipy import signal

from .filters import FiltersSettings, design_sos
from .response_canvas import ResponseCanvas

COLOURS = {"highpass": "#63dfc0", "lowpass": "#73a8ff", "notch": "#eabb6b"}


class FilterMarker(pg.InfiniteLine):
    selected = QtCore.Signal(int)
    adjusted = QtCore.Signal(int, float)

    def __init__(self, index):
        super().__init__(angle=90, movable=True, pen=pg.mkPen("#ffffff", width=2))
        self.index = index
        self.label = pg.InfLineLabel(self, text="", position=0.9, color="#ffffff")
        self.label.hide()

    def mouseClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            event.accept()
            self.selected.emit(self.index)
        else:
            super().mouseClickEvent(event)

    def mouseDragEvent(self, event):
        if event.isStart():
            self.selected.emit(self.index)
        super().mouseDragEvent(event)

    def wheelEvent(self, event):
        self.adjusted.emit(self.index, event.delta() / 120)
        event.accept()

    def hoverEvent(self, event):
        super().hoverEvent(event)
        self.label.setVisible(not event.isExit())


def response_points(settings, rate):
    """Include narrow notch centres and nearby widths, not just a uniform grid."""
    high = rate / 2 - 0.001
    points = [np.geomspace(20, high, 2048)]
    for item in settings.filters:
        points.append(np.array([item.frequency]))
        if item.kind == "notch":
            points.append(item.frequency + np.linspace(-2, 2, 129) * item.frequency / item.q)
    frequencies = np.unique(np.clip(np.concatenate(points), 20, high))
    sos = design_sos(settings, rate)
    if not len(sos):
        return frequencies, np.zeros_like(frequencies)
    _, response = signal.sosfreqz(sos, worN=frequencies, fs=rate)
    return frequencies, 20 * np.log10(np.maximum(np.abs(response), 1e-12))


class FilterCanvas(ResponseCanvas):
    placed = QtCore.Signal(float)
    selected = QtCore.Signal(int)
    moved = QtCore.Signal(int, float)
    adjusted = QtCore.Signal(int, float)

    def __init__(self, axis_type):
        super().__init__(axis_type)
        self.setLabel("left", "Filter response", units="dB")
        self.getViewBox().setLimits(yMin=-120, yMax=20)
        self.setYRange(-72, 12, padding=0)
        self.response = self.plot(pen=pg.mkPen("#63dfc0", width=3))
        self.individual = self.plot(pen=pg.mkPen("#ffffff", width=1))
        legend = self.addLegend(colCount=2, offset=(10, 10))
        for curve, name in ((self.input_curve, "Input spectrum (relative)"),
                            (self.output_curve, "Output spectrum (relative)"),
                            (self.response, "Combined response"),
                            (self.individual, "Selected filter")):
            legend.addItem(curve, name)
        self.markers = []
        self.rate = 48000
        self.updating = False
        self.scene().sigMouseClicked.connect(self.plot_clicked)
        self.setToolTip(
            "Click to add the chosen filter type. Drag a line for frequency; "
            "scroll over it for slope or notch Q. Background spectra show relative levels."
        )

    def plot_clicked(self, event):
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and not event.isAccepted()
            and self.getViewBox().sceneBoundingRect().contains(event.scenePos())
        ):
            point = self.getViewBox().mapSceneToView(event.scenePos())
            self.placed.emit(float(np.clip(10 ** point.x(), 20, self.rate / 2 - 0.001)))

    def marker_moved(self, marker):
        if not self.updating:
            self.moved.emit(marker.index, float(10 ** marker.value()))

    def set_settings(self, settings, rate, selected=-1, bypassed=False):
        self.rate = rate
        high = rate / 2 - 0.001
        self.getViewBox().setLimits(xMax=np.log10(high))
        self.setXRange(np.log10(20), np.log10(high), padding=0)
        self.updating = True
        try:
            while len(self.markers) > len(settings.filters):
                self.removeItem(self.markers.pop())
            while len(self.markers) < len(settings.filters):
                marker = FilterMarker(len(self.markers))
                marker.sigPositionChangeFinished.connect(self.marker_moved)
                marker.selected.connect(self.selected)
                marker.adjusted.connect(self.adjusted)
                self.addItem(marker)
                self.markers.append(marker)
            for index, (marker, item) in enumerate(zip(self.markers, settings.filters, strict=True)):
                marker.setBounds((np.log10(20), np.log10(high)))
                marker.setValue(np.log10(item.frequency))
                marker.label.setFormat(f"{index + 1}. {item.kind} {item.frequency:g} Hz")
                marker.setPen(pg.mkPen("#ffffff" if selected == index else COLOURS[item.kind], width=2))
                marker.setOpacity(0.35 if bypassed or not item.enabled else 1)
            frequencies, db = response_points(settings, rate)
            self.response.setData(frequencies, np.zeros_like(db) if bypassed else db)
            self.response.setPen(pg.mkPen("#647085" if bypassed else "#63dfc0", width=3))
            if 0 <= selected < len(settings.filters) and not bypassed:
                item = settings.filters[selected]
                self.individual.setData(*response_points(FiltersSettings((item,)), rate))
                self.individual.setPen(pg.mkPen(COLOURS[item.kind], width=1, style=QtCore.Qt.PenStyle.DashLine))
            else:
                self.individual.clear()
            self.setTitle("Corrective filters | whole recording" + (" | bypassed" if bypassed else ""))
        finally:
            self.updating = False
