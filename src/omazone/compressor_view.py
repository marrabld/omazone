"""Compressor inspector and plots for the static curve and rendered gain history."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .compressor import (
    CompressorSettings,
    compressor_parameters,
    gain_reduction_db,
    settings_from_parameters,
    validate_settings,
)
from .engine import peak_db


class CompressorCanvas(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.transfer = pg.PlotWidget()
        self.transfer.setLabel("left", "Output level", units="dBFS")
        self.transfer.setLabel("bottom", "Input level", units="dBFS")
        self.transfer.setTitle("Compression curve | input to output")
        self.transfer.showGrid(x=True, y=True, alpha=0.12)
        self.transfer.setXRange(-60, 6, padding=0)
        self.transfer.setYRange(-60, 6, padding=0)
        self.transfer.getViewBox().setMouseEnabled(x=False, y=False)
        self.transfer.plot(
            [-60, 6], [-60, 6], pen=pg.mkPen("#647085", style=QtCore.Qt.PenStyle.DashLine)
        )
        self.curve = self.transfer.plot(pen=pg.mkPen("#63dfc0", width=3))
        self.threshold = pg.InfiniteLine(
            angle=90, pen=pg.mkPen("#eabb6b", style=QtCore.Qt.PenStyle.DashLine)
        )
        self.transfer.addItem(self.threshold)
        layout.addWidget(self.transfer, 2)
        self.detector = pg.PlotWidget()
        self.detector.setTitle("Linked detector envelope | dBFS")
        self.detector.setLabel("left", "Detector", units="dBFS")
        self.detector.showGrid(x=True, y=True, alpha=0.12)
        self.detector_curve = self.detector.plot(pen=pg.mkPen("#73a8ff", width=1.5))
        layout.addWidget(self.detector, 1)
        self.reduction = pg.PlotWidget()
        self.reduction.setTitle("Gain reduction | negative dB")
        self.reduction.setLabel("left", "Reduction", units="dB")
        self.reduction.setLabel("bottom", "Song time", units="s")
        self.reduction.showGrid(x=True, y=True, alpha=0.12)
        self.reduction.setYRange(-18, 0, padding=0)
        self.reduction_curve = self.reduction.plot(pen=pg.mkPen("#eabb6b", width=2))
        self.reduction.setXLink(self.detector)
        layout.addWidget(self.reduction, 1)
        self.cursors = []
        for plot in (self.detector, self.reduction):
            cursor = pg.InfiniteLine(pos=0, pen=pg.mkPen(216, 225, 237, 100))
            plot.addItem(cursor)
            self.cursors.append(cursor)
        self.current_result = None
        for plot in (self.detector, self.reduction):
            plot.getViewBox().setMouseEnabled(x=True, y=False)
        self.setMinimumHeight(270)

    def show_settings(self, settings, bypassed):
        level = np.linspace(-60, 6, 500)
        reduction = gain_reduction_db(level, settings)
        self.curve.setData(level, level if bypassed else level + reduction + settings.makeup_db)
        self.curve.setPen(pg.mkPen("#647085" if bypassed else "#63dfc0", width=3))
        self.threshold.setValue(settings.threshold_db)
        self.transfer.setYRange(
            min(-60, -60 + settings.makeup_db), max(6, 6 + settings.makeup_db), padding=0
        )
        self.transfer.setTitle(
            "Compression bypassed" if bypassed else "Compression curve | input to output"
        )

    def show_result(self, result):
        if result is self.current_result:
            return
        self.current_result = result
        if result is None:
            self.detector_curve.clear()
            self.reduction_curve.clear()
            return
        self.detector_curve.setData(result.time, result.detector_db)
        self.reduction_curve.setData(result.time, result.reduction_db)
        self.reduction.setYRange(min(-18, float(np.min(result.reduction_db)) - 2), 0, padding=0)
        end = max(0.1, result.time[-1])
        self.detector.setXRange(0, end, padding=0)

    def set_position(self, seconds):
        for cursor in self.cursors:
            cursor.setValue(seconds)


class CompressorView(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.restoring = False
        self.canvas = CompressorCanvas()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        prompt = QtWidgets.QLabel(
            "Control whole-mix level changes after EQ. Start gently and watch gain reduction."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        self.threshold = self.spin(layout, "Threshold", -60, 0, -18, " dBFS", 1)
        self.ratio = self.spin(layout, "Ratio", 1, 20, 2, ":1", 1)
        self.makeup = self.spin(layout, "Manual makeup gain", -18, 18, 0, " dB", 1)
        self.advanced_toggle = QtWidgets.QToolButton()
        self.advanced_toggle.setText("Timing, knee and detector")
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced = QtWidgets.QWidget()
        more = QtWidgets.QVBoxLayout(self.advanced)
        self.attack = self.spin(more, "Attack", 0, 200, 10, " ms", 1)
        self.release = self.spin(more, "Release", 1, 3000, 120, " ms", 1)
        self.knee = self.spin(more, "Soft knee", 0, 24, 6, " dB", 1)
        more.addWidget(QtWidgets.QLabel("Stereo-linked detector"))
        self.detector = QtWidgets.QComboBox()
        self.detector.addItem("Peak (largest channel sample)", "peak")
        self.detector.addItem("RMS (channel energy)", "rms")
        more.addWidget(self.detector)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.enabled = QtWidgets.QCheckBox("Apply compressor")
        layout.addWidget(self.enabled)
        self.listen_button = QtWidgets.QPushButton("Loop and compare compression")
        self.listen_button.clicked.connect(self.listen)
        layout.addWidget(self.listen_button)
        self.summary = QtWidgets.QLabel(
            "Compressor skipped. Set the controls and render to hear it."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.render_button = QtWidgets.QPushButton("Apply compression and render")
        self.render_button.clicked.connect(owner.render_saved_recipe)
        for control in (
            self.threshold,
            self.ratio,
            self.makeup,
            self.attack,
            self.release,
            self.knee,
        ):
            control.valueChanged.connect(self.changed)
        self.detector.currentIndexChanged.connect(self.changed)
        self.enabled.toggled.connect(self.changed)
        self.restore()

    def spin(self, layout, name, low, high, value, suffix, decimals):
        layout.addWidget(QtWidgets.QLabel(name))
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(low, high)
        control.setDecimals(decimals)
        control.setValue(value)
        control.setSuffix(suffix)
        layout.addWidget(control)
        return control

    def settings(self):
        return CompressorSettings(
            self.threshold.value(),
            self.ratio.value(),
            self.attack.value(),
            self.release.value(),
            self.knee.value(),
            self.makeup.value(),
            self.detector.currentData(),
        )

    def restore(self):
        self.restoring = True
        try:
            try:
                settings = settings_from_parameters(
                    self.owner.project.stages["dynamics"].parameters
                )
                validate_settings(settings)
                self.summary.setText("Compressor settings restored. Render to hear this step.")
            except (TypeError, ValueError) as error:
                settings = CompressorSettings()
                self.summary.setText(str(error))
            for control, value in (
                (self.threshold, settings.threshold_db),
                (self.ratio, settings.ratio),
                (self.attack, settings.attack_ms),
                (self.release, settings.release_ms),
                (self.knee, settings.knee_db),
                (self.makeup, settings.makeup_db),
            ):
                with QtCore.QSignalBlocker(control):
                    control.setValue(value)
            with QtCore.QSignalBlocker(self.detector):
                self.detector.setCurrentIndex(self.detector.findData(settings.detector))
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(not self.owner.project.stages["dynamics"].bypassed)
        finally:
            self.restoring = False
        self.draw()

    def changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        self.owner.project.stages["dynamics"].parameters = compressor_parameters(self.settings())
        self.owner.project.stages["dynamics"].bypassed = not self.enabled.isChecked()
        self.owner.invalidate_dynamics()
        self.draw()
        self.summary.setText(
            "Compression edited. Earlier EQ and matching are retained. Render to listen."
        )

    def draw(self):
        self.canvas.show_settings(self.settings(), self.owner.project.stages["dynamics"].bypassed)
        self.canvas.show_result(
            self.owner.chain_result.compression
            if self.owner.chain_result is not None and self.owner.dynamics_preview is not None
            else None
        )

    def rendered(self):
        result = self.owner.chain_result
        self.draw()
        if result is None or result.compression is None:
            self.summary.setText("Compressor skipped. Export uses the earlier chain.")
            return
        deepest = result.compression.max_reduction_db
        peak = peak_db(result.output)
        self.summary.setText(
            f"Maximum reduction: {deepest:.1f} dB. Output sample peak: {peak:.1f} dBFS."
            + (
                " Output exceeds 0 dBFS; there is no limiter."
                if peak > 0
                else " Compare this step or export."
            )
        )

    def listen(self):
        if self.owner.dynamics_preview is None:
            return
        self.owner.preview_mode.setCurrentIndex(self.owner.preview_mode.findData("dynamics"))
        if self.owner.waveform.selection:
            self.owner.play_selection()
        else:
            self.owner.play()
