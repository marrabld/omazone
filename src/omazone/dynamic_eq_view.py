"""One whole-recording dynamic bell, independent of the compressor recipes."""

from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .dynamic_eq import (
    DynamicEQSettings,
    dynamic_eq_parameters,
    response,
    settings_from_parameters,
    validate_settings,
)


class DynamicEQCanvas(QtWidgets.QWidget):
    def __init__(self, frequency_axis):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.response_plot = pg.PlotWidget(
            axisItems={"bottom": frequency_axis(orientation="bottom")}
        )
        self.response_plot.setLogMode(x=True)
        self.response_plot.setLabel("left", "Gain", units="dB")
        self.response_plot.setLabel("bottom", "Frequency")
        self.response_plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.reduction_plot = pg.PlotWidget()
        self.reduction_plot.setLabel("left", "Gain", units="dB")
        self.reduction_plot.setLabel("bottom", "Song time", units="s")
        for plot in (self.response_plot, self.reduction_plot):
            plot.setMinimumHeight(170)
            plot.showGrid(x=True, y=True, alpha=0.15)
            layout.addWidget(plot, 1)
        self.cursor = pg.InfiniteLine(pos=0, pen=pg.mkPen("#ffffff"))

    def draw(self, settings, rate, result, bypassed):
        self.response_plot.clear()
        frequency = np.geomspace(20, min(20000, rate * 0.49), 512)
        self.response_plot.plot(frequency, np.zeros(len(frequency)), pen=pg.mkPen("#647b9a"))
        self.response_plot.setTitle(
            "Potential bell response | maximum cut" + (" | skipped" if bypassed else "")
        )
        try:
            gain = 20 * np.log10(np.maximum(np.abs(response(settings, rate, frequency)), 1e-12))
            self.response_plot.plot(
                frequency, gain, pen=pg.mkPen("#647b9a" if bypassed else "#63dfc0", width=2)
            )
        except ValueError:
            pass  # The inspector explains an invalid recipe; never draw a guessed replacement.
        self.response_plot.setXRange(np.log10(20), np.log10(frequency[-1]), padding=0)
        self.response_plot.setYRange(-settings.max_cut_db - 1, 1, padding=0)
        self.reduction_plot.clear()
        self.reduction_plot.setTitle(
            "Dynamic EQ skipped | no cut applied"
            if bypassed
            else "Rendered bell gain | whole recording"
            if result is not None
            else "Render to see actual bell gain"
        )
        if result is not None:
            self.reduction_plot.plot(result.time, result.reduction_db, pen=pg.mkPen("#eabb6b"))
        self.reduction_plot.setYRange(-settings.max_cut_db - 1, 1, padding=0)
        self.reduction_plot.addItem(self.cursor)

    def set_position(self, seconds):
        self.cursor.setValue(seconds)


class DynamicEQView(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.restoring = False
        self.invalid_recipe = None
        self.value = DynamicEQSettings()
        self.draw_key = None
        self.canvas = DynamicEQCanvas(type(owner.spectrum_plot.getAxis("bottom")))
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        back = QtWidgets.QPushButton("Back to compression")
        back.clicked.connect(lambda: owner.views.setStep("dynamics"))
        layout.addWidget(back)
        prompt = QtWidgets.QLabel(
            "Reduce an intermittently excessive frequency range. One downward bell after corrective filters and before compression. Passage selection affects audition and measurements, not processing scope."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        self.enabled = QtWidgets.QCheckBox("Apply dynamic EQ")
        layout.addWidget(self.enabled)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        self.controls = {}
        for field, title, low, high, suffix, decimals in (
            ("frequency", "Frequency", 20, 20000, " Hz", 3),
            ("q", "Bandwidth Q", 0.5, 20, "", 3),
            ("threshold_db", "Threshold", -60, 0, " dBFS", 2),
            ("max_cut_db", "Maximum cut", 0, 18, " dB", 2),
            ("attack_ms", "Attack", 0.1, 200, " ms", 2),
            ("release_ms", "Release", 1, 3000, " ms", 2),
        ):
            control = QtWidgets.QDoubleSpinBox()
            control.setDecimals(decimals)
            control.setRange(low, high)
            control.setSuffix(suffix)
            self.controls[field] = control
            label = QtWidgets.QLabel(title)
            label.setProperty("formLabel", True)
            label.setMinimumWidth(label.fontMetrics().horizontalAdvance(title) + 12)
            form.addRow(label, control)
            control.valueChanged.connect(lambda value, field=field: self.edit(field, value))
        note = QtWidgets.QLabel(
            "Linked band-peak detector: 1 dB of cut per dB above threshold, capped at Maximum cut. No upward processing or makeup gain. Fixed IIR poles; no buffering delay. Render to apply changes."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.listen_button = QtWidgets.QPushButton("Listen and compare dynamic EQ")
        self.listen_button.clicked.connect(self.listen)
        layout.addWidget(self.listen_button)
        reset = QtWidgets.QPushButton("Reset band and skip")
        reset.clicked.connect(self.reset_band)
        layout.addWidget(reset)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.render_button = QtWidgets.QPushButton("Render dynamic EQ")
        self.render_button.clicked.connect(owner.render_saved_recipe)
        self.enabled.toggled.connect(self.toggle)
        self.restore()

    @property
    def rate(self):
        return self.owner.source[1] if self.owner.source else 48000

    def restore(self):
        self.restoring = True
        self.invalid_recipe = None
        try:
            try:
                self.value = settings_from_parameters(
                    self.owner.project.stages["dynamic-eq"].parameters, self.rate
                )
                validate_settings(self.value, self.rate)
            except ValueError as error:
                self.invalid_recipe = str(error)
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(not self.owner.project.stages["dynamic-eq"].bypassed)
            self.controls["frequency"].setMaximum(
                max(min(20000, self.rate * 0.45), self.value.frequency)
            )
            for field, control in self.controls.items():
                with QtCore.QSignalBlocker(control):
                    control.setValue(getattr(self.value, field))
                control.setEnabled(self.invalid_recipe is None)
        finally:
            self.restoring = False
        self.draw()

    def edit(self, field, value):
        if self.restoring or self.owner.restoring_project or self.invalid_recipe:
            return
        chosen = replace(self.value, **{field: value})
        validate_settings(chosen, self.rate)
        self.value = chosen
        stage = self.owner.project.stages["dynamic-eq"]
        stage.parameters = dynamic_eq_parameters(chosen)
        stage.bypassed = False
        with QtCore.QSignalBlocker(self.enabled):
            self.enabled.setChecked(True)
        self.owner.invalidate_dynamic_eq()

    def toggle(self, enabled):
        if not self.restoring and not self.owner.restoring_project:
            self.owner.set_stage_bypass("dynamic-eq", not enabled)

    def reset_band(self):
        stage = self.owner.project.stages["dynamic-eq"]
        stage.parameters = dynamic_eq_parameters(settings_from_parameters({}, self.rate))
        stage.bypassed = True
        self.value = DynamicEQSettings(frequency=min(2400, self.rate / 4))
        self.owner.invalidate_dynamic_eq()
        self.restore()

    def draw(self):
        result = self.owner.dynamic_eq_result
        key = (
            self.value,
            self.rate,
            id(result),
            self.owner.project.stages["dynamic-eq"].bypassed,
            self.invalid_recipe,
        )
        if self.invalid_recipe:
            if key != self.draw_key:
                self.canvas.response_plot.clear()
                self.canvas.response_plot.setTitle("Saved dynamic EQ settings are unavailable")
                self.canvas.reduction_plot.clear()
                self.canvas.reduction_plot.setTitle("Reset or correct the saved recipe to render")
            self.draw_key = key
            self.summary.setText(self.invalid_recipe)
            return
        if key != self.draw_key:
            self.canvas.draw(
                self.value, self.rate, result, self.owner.project.stages["dynamic-eq"].bypassed
            )
            self.draw_key = key
        self.summary.setText(
            self.invalid_recipe
            or (
                f"Maximum rendered cut: {result.max_reduction_db:.2f} dB. A/B isolates dynamic EQ; export includes the full chain."
                if result is not None
                else self.owner.workflow_status().stages["dynamic-eq"].reason
            )
        )

    def listen(self):
        if self.owner.comparison("dynamic-eq").available:
            self.owner.apply_comparison("dynamic-eq")
            if self.owner.waveform.selection:
                self.owner.play_selection()
            else:
                self.owner.play()
