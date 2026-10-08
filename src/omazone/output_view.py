"""Explicit final gain, sample-peak context and output-stage comparison."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .output_gain import (
    OutputGainSettings,
    output_parameters,
    sample_peak,
    settings_from_parameters,
    validate_settings,
)


class OutputCanvas(pg.PlotWidget):
    def __init__(self):
        super().__init__()
        self.setTitle("Sample peaks | source, pre-output and final export")
        self.setLabel("left", "Sample peak", units="dBFS")
        self.getAxis("bottom").setTicks([[(1, "Source"), (2, "Before gain"), (3, "Export")]])
        self.setXRange(0.45, 3.55, padding=0)
        self.setYRange(-60, 6, padding=0)
        self.getViewBox().setMouseEnabled(x=False, y=False)
        self.showGrid(x=False, y=True, alpha=0.15)
        self.addItem(
            pg.InfiniteLine(
                pos=0, angle=0, pen=pg.mkPen("#ff9086", style=QtCore.Qt.PenStyle.DashLine)
            )
        )
        self.bars = []
        self.labels = []
        for x in (1, 2, 3):
            bar = pg.BarGraphItem(
                x=[x],
                y0=-60,
                height=[0],
                width=0.55,
                brush=pg.mkBrush("#647085"),
                pen=pg.mkPen("#647085"),
            )
            self.addItem(bar)
            self.bars.append(bar)
            label = pg.TextItem("", anchor=(0.5, 1), color="#d8e1ed")
            self.addItem(label)
            self.labels.append(label)

    def show_peaks(self, peaks):
        valid = [20 * np.log10(peak) for peak in peaks if peak is not None and peak > 0]
        self.setYRange(-60, max(6, max(valid, default=0) + 5), padding=0)
        for bar, label, x, peak in zip(self.bars, self.labels, (1, 2, 3), peaks, strict=True):
            value = 20 * np.log10(peak) if peak is not None and peak > 0 else float("-inf")
            colour = (
                "#647085"
                if peak is None
                else "#ff9086"
                if peak > 1
                else "#73a8ff"
                if x == 1
                else "#eabb6b"
                if x == 2
                else "#63dfc0"
            )
            bar.setOpts(
                height=max(0, value + 60) if peak is not None else 0,
                brush=pg.mkBrush(colour),
                pen=pg.mkPen(colour),
            )
            label.setText(
                "--" if peak is None else "silence" if peak == 0 else f"{value:+.1f} dBFS"
            )
            label.setPos(x, max(-56, value + 2) if peak is not None else -56)


class OutputView(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.canvas = OutputCanvas()
        self.restoring = False
        self.cached_source = None
        self.source_peak = None
        self.measured_result = None
        self.before_peak = self.final_peak = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        prompt = QtWidgets.QLabel(
            "Set final gain and check peaks before export. Red means samples exceed 0 dBFS."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        layout.addWidget(QtWidgets.QLabel("Output gain"))
        self.gain = QtWidgets.QDoubleSpinBox()
        self.gain.setRange(-36, 18)
        self.gain.setDecimals(1)
        self.gain.setSuffix(" dB")
        layout.addWidget(self.gain)
        self.enabled = QtWidgets.QCheckBox("Apply output gain")
        layout.addWidget(self.enabled)
        self.listen_button = QtWidgets.QPushButton("Loop and compare output gain")
        self.listen_button.clicked.connect(self.listen)
        layout.addWidget(self.listen_button)
        self.readouts = [QtWidgets.QLabel() for _ in range(3)]
        for label in self.readouts:
            label.setWordWrap(True)
            layout.addWidget(label)
        self.summary = QtWidgets.QLabel(
            "Render to check final peaks. This stage does not limit them."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.render_button = QtWidgets.QPushButton("Apply output gain and render")
        self.render_button.clicked.connect(owner.render_saved_recipe)
        self.gain.valueChanged.connect(self.gain_changed)
        self.enabled.toggled.connect(self.changed)
        self.restore()

    def settings(self):
        return OutputGainSettings(self.gain.value())

    def restore(self):
        self.restoring = True
        try:
            try:
                settings = settings_from_parameters(self.owner.project.stages["output"].parameters)
                validate_settings(settings)
                self.summary.setText("Output choices restored. Render to measure the final file.")
            except (TypeError, ValueError) as error:
                settings = OutputGainSettings()
                self.summary.setText(str(error))
            with QtCore.QSignalBlocker(self.gain):
                self.gain.setValue(settings.gain_db)
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(not self.owner.project.stages["output"].bypassed)
        finally:
            self.restoring = False
        self.draw()

    def gain_changed(self, value):
        if not self.restoring and not self.supported_recipe():
            with QtCore.QSignalBlocker(self.gain):
                self.gain.setValue(0)
            self.summary.setText(
                "Saved output settings are unsupported. Choices retained; gain cannot replace them silently."
            )
            return
        if not self.restoring and value != 0 and not self.enabled.isChecked():
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(True)
        self.changed()

    def supported_recipe(self):
        try:
            validate_settings(
                settings_from_parameters(self.owner.project.stages["output"].parameters)
            )
            return True
        except (TypeError, ValueError):
            return False

    def changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        stage = self.owner.project.stages["output"]
        if self.supported_recipe():
            stage.parameters = output_parameters(self.settings())
        stage.bypassed = not self.enabled.isChecked()
        self.owner.invalidate_output()
        self.summary.setText(self.owner.workflow_status().stages["output"].reason)

    @staticmethod
    def reading(name, peak):
        if peak is None:
            return f"{name}: render to measure"
        if peak == 0:
            return f"{name}: silence"
        db = 20 * np.log10(peak)
        return f"{name}: {db:+.1f} dBFS sample peak" + (" | OVER 0 dBFS" if peak > 1 else "")

    def draw(self):
        source = self.owner.source[0] if self.owner.source is not None else None
        if source is not self.cached_source:
            self.cached_source = source
            self.source_peak = sample_peak(source) if source is not None else None
        status = self.owner.workflow_status().stages["output"]
        result = self.owner.chain_result if status.measurements_available else None
        if result is not self.measured_result:
            self.measured_result = result
            self.before_peak = sample_peak(result.pre_output) if result is not None else None
            self.final_peak = sample_peak(result.output) if result is not None else None
        peaks = (self.source_peak, self.before_peak, self.final_peak)
        self.canvas.show_peaks(peaks)
        for label, name, peak in zip(
            self.readouts,
            ("Original source", "Before output gain", "Final export"),
            peaks,
            strict=True,
        ):
            label.setText(self.reading(name, peak))
            label.setStyleSheet("color: #ff9086;" if peak is not None and peak > 1 else "")

    def rendered(self):
        self.draw()
        result = self.owner.chain_result
        if result is None:
            return
        final = sample_peak(result.output)
        if final > 1:
            self.summary.setText(
                "Final float export exceeds 0 dBFS. Lower output gain or revisit earlier stages; no limiter is active."
            )
        elif self.source_peak is not None and self.source_peak > 1:
            self.summary.setText(
                "Final samples fit below 0 dBFS. Earlier source overload may still contain flattened peaks."
            )
        else:
            self.summary.setText(
                "Final sample peaks fit below 0 dBFS. True peaks and LUFS are not measured yet."
            )

    def listen(self):
        if not self.owner.comparison("output-gain").available:
            return
        self.owner.apply_comparison("output-gain")
        if self.owner.waveform.selection:
            self.owner.play_selection()
        else:
            self.owner.play()
