"""Compact one-band corrective EQ controls, after matching in the project chain."""

from dataclasses import asdict

import numpy as np
from PySide6 import QtCore, QtWidgets

from .manual_eq import BellSettings, frequency_response, settings_from_parameters, validate_settings


class ManualEQView(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.restoring = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        prompt = QtWidgets.QLabel(
            "Correct the tone after matching. Start with a small cut and compare this step."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        layout.addWidget(QtWidgets.QLabel("Apply to"))
        self.region = QtWidgets.QComboBox()
        self.region.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.region.setMinimumContentsLength(16)
        layout.addWidget(self.region)
        self.frequency = self.spin(layout, "Frequency", 20, 20000, 2400, " Hz", 1)
        self.gain = self.spin(layout, "Gain", -18, 18, 0, " dB", 1)
        self.enabled = QtWidgets.QCheckBox("Apply this EQ")
        layout.addWidget(self.enabled)
        self.advanced_toggle = QtWidgets.QToolButton()
        self.advanced_toggle.setText("Width and transitions")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.advanced_toggle)
        self.advanced_panel = QtWidgets.QWidget()
        advanced = QtWidgets.QVBoxLayout(self.advanced_panel)
        self.q = self.spin(advanced, "Width (Q)", 0.1, 20, 1, "", 2)
        self.transition = self.spin(advanced, "Fade inside region", 0, 5000, 75, " ms", 1)
        layout.addWidget(self.advanced_panel)
        self.advanced_panel.hide()
        self.advanced_toggle.toggled.connect(self.advanced_panel.setVisible)
        self.listen_button = QtWidgets.QPushButton("Loop and compare this step")
        self.listen_button.clicked.connect(self.listen)
        layout.addWidget(self.listen_button)
        self.reset_button = QtWidgets.QPushButton("Reset band to neutral")
        self.reset_button.clicked.connect(lambda: self.gain.setValue(0))
        layout.addWidget(self.reset_button)
        self.summary = QtWidgets.QLabel("Neutral until you choose a correction.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.render_button = QtWidgets.QPushButton("Apply EQ and render")
        self.render_button.clicked.connect(owner.render_saved_recipe)
        for control in (self.frequency, self.gain, self.q, self.transition):
            control.valueChanged.connect(self.changed)
        self.gain.valueChanged.connect(self.enable_from_gain)
        self.region.currentIndexChanged.connect(self.changed)
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
        return BellSettings(
            self.frequency.value(),
            self.gain.value(),
            self.q.value(),
            self.region.currentData(),
            self.transition.value(),
        )

    def refresh_regions(self):
        selected = self.region.currentData()
        with QtCore.QSignalBlocker(self.region):
            self.region.clear()
            self.region.addItem("Whole recording", None)
            for region in self.owner.project.regions:
                self.region.addItem(region.name, region.id)
            settings = self.owner.project.stages["eq"].parameters.get("band", {})
            saved = settings.get("region_id", selected)
            index = self.region.findData(saved)
            if saved is not None and index < 0:
                self.region.addItem("Missing saved region", saved)
                index = self.region.count() - 1
            self.region.setCurrentIndex(max(0, index))

    def restore(self):
        self.restoring = True
        try:
            self.refresh_regions()
            parameters = self.owner.project.stages["eq"].parameters
            rate = self.owner.source[1] if self.owner.source else 48000
            try:
                settings = settings_from_parameters(parameters)
                validate_settings(settings, rate, self.owner.project.regions)
                self.summary.setText("EQ choices restored; render to hear this step.")
            except (TypeError, ValueError) as error:
                settings = BellSettings()
                self.summary.setText(str(error))
            with QtCore.QSignalBlocker(self.frequency):
                self.frequency.setRange(20, min(20000, rate / 2 - 1))
            for control, value in (
                (self.frequency, settings.frequency),
                (self.gain, settings.gain_db),
                (self.q, settings.q),
                (self.transition, settings.transition_ms),
            ):
                with QtCore.QSignalBlocker(control):
                    control.setValue(value)
            self.frequency.setToolTip(f"Must stay below Nyquist ({rate / 2:g} Hz).")
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(not self.owner.project.stages["eq"].bypassed)
        finally:
            self.restoring = False

    def enable_from_gain(self, gain):
        if not self.restoring and not self.owner.restoring_project and gain != 0:
            self.enabled.setChecked(True)

    def changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        settings = self.settings()
        self.owner.project.stages["eq"].parameters = {"kind": "bell-v1", "band": asdict(settings)}
        self.owner.project.stages["eq"].bypassed = not self.enabled.isChecked()
        self.owner.invalidate_eq()
        self.draw_response()
        region_id = settings.region_id
        region = next((item for item in self.owner.project.regions if item.id == region_id), None)
        if region is not None:
            self.owner.waveform.set_selection(region.bounds)
        self.summary.setText(
            "EQ edited. Matching and repairs are retained. Render before comparing."
        )

    def draw_response(self):
        if self.owner.source is None:
            return
        try:
            settings = settings_from_parameters(self.owner.project.stages["eq"].parameters)
            frequency, db = frequency_response(settings, self.owner.source[1])
        except ValueError as error:
            self.summary.setText(str(error))
            return
        self.owner.eq_plot.clear()
        bypassed = self.owner.project.stages["eq"].bypassed
        if bypassed:
            db = np.zeros_like(db)
        self.owner.eq_plot.setTitle(
            "Manual EQ bypassed"
            if bypassed
            else "Manual EQ | region-limited"
            if settings.region_id
            else "Manual EQ | whole recording"
        )
        self.owner.eq_plot.plot(frequency[1:], db[1:], pen="#63dfc0", name="Bell response")
        self.owner.eq_plot.setYRange(-19, 19, padding=0)
        region = next(
            (item.bounds for item in self.owner.project.regions if item.id == settings.region_id),
            None,
        )
        self.owner.waveform.set_eq_scope(
            None if bypassed else region,
            settings.transition_ms,
            settings.gain_db,
            settings.frequency,
        )

    def listen(self):
        if self.owner.eq_preview is None:
            return
        settings = settings_from_parameters(self.owner.project.stages["eq"].parameters)
        region = next(
            (item for item in self.owner.project.regions if item.id == settings.region_id), None
        )
        if region:
            self.owner.waveform.set_selection(region.bounds)
            self.owner.loop_selection.setChecked(True)
        self.owner.preview_mode.setCurrentIndex(self.owner.preview_mode.findData("eq"))
        if self.owner.waveform.selection:
            self.owner.play_selection()
        else:
            self.owner.play()
