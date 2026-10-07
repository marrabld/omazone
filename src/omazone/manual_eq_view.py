"""Multi-band corrective EQ inspector and interactive plot controls."""

from dataclasses import replace

from PySide6 import QtCore, QtWidgets

from .eq_canvas import EQCanvas
from .manual_eq import (
    MAX_BANDS,
    BellSettings,
    EQSettings,
    eq_from_parameters,
    eq_parameters,
    validate_eq,
)


class ManualEQView(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.restoring = False
        self.bands = []
        self.selected = -1
        self.canvas = EQCanvas(type(owner.spectrum_plot.getAxis("bottom")))
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        prompt = QtWidgets.QLabel(
            "Click the graph to add a band. Drag its dot to set frequency and gain; scroll over it to change width."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        layout.addWidget(QtWidgets.QLabel("Apply to"))
        self.region = QtWidgets.QComboBox()
        layout.addWidget(self.region)
        layout.addWidget(QtWidgets.QLabel("Bands"))
        self.band_list = QtWidgets.QListWidget()
        self.band_list.setMaximumHeight(115)
        layout.addWidget(self.band_list)
        row = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("Add band")
        self.duplicate_button = QtWidgets.QPushButton("Duplicate")
        self.remove_button = QtWidgets.QPushButton("Remove")
        for button in (self.add_button, self.duplicate_button, self.remove_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.frequency = self.spin(layout, "Frequency", 20, 20000, 2400, " Hz", 1)
        self.gain = self.spin(layout, "Gain", -18, 18, 0, " dB", 1)
        self.q = self.spin(layout, "Width (Q)", 0.1, 20, 1, "", 2)
        self.band_enabled = QtWidgets.QCheckBox("Apply selected band")
        layout.addWidget(self.band_enabled)
        self.enabled = QtWidgets.QCheckBox("Apply this EQ")
        layout.addWidget(self.enabled)
        self.advanced_toggle = QtWidgets.QToolButton()
        self.advanced_toggle.setText("Region transition")
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced_panel = QtWidgets.QWidget()
        advanced = QtWidgets.QVBoxLayout(self.advanced_panel)
        self.transition = self.spin(advanced, "Fade inside region", 0, 5000, 75, " ms", 1)
        layout.addWidget(self.advanced_panel)
        self.advanced_panel.hide()
        self.advanced_toggle.toggled.connect(self.advanced_panel.setVisible)
        self.listen_button = QtWidgets.QPushButton("Loop and compare this step")
        self.listen_button.clicked.connect(self.listen)
        layout.addWidget(self.listen_button)
        self.reset_button = QtWidgets.QPushButton("Reset selected band to neutral")
        self.reset_button.clicked.connect(lambda: self.gain.setValue(0))
        layout.addWidget(self.reset_button)
        self.summary = QtWidgets.QLabel("Click the graph or Add band to start.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.render_button = QtWidgets.QPushButton("Apply EQ and render")
        self.render_button.clicked.connect(owner.render_saved_recipe)
        for control in (self.frequency, self.gain, self.q):
            control.valueChanged.connect(self.edit_band)
        for control in (self.transition, self.region):
            (
                control.valueChanged if control is self.transition else control.currentIndexChanged
            ).connect(self.changed)
        self.enabled.toggled.connect(self.changed)
        self.band_enabled.toggled.connect(self.edit_band)
        self.band_list.currentRowChanged.connect(self.select_band)
        self.add_button.clicked.connect(lambda: self.add_band())
        self.duplicate_button.clicked.connect(self.duplicate_band)
        self.remove_button.clicked.connect(self.remove_band)
        self.canvas.placed.connect(self.add_band)
        self.canvas.selected.connect(self.band_list.setCurrentRow)
        self.canvas.moved.connect(self.move_band)
        self.canvas.committed.connect(lambda _: self.changed())
        self.canvas.width_changed.connect(self.change_width)
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
        return EQSettings(tuple(self.bands), self.region.currentData(), self.transition.value())

    def refresh_regions(self):
        saved = self.region.currentData()
        with QtCore.QSignalBlocker(self.region):
            self.region.clear()
            self.region.addItem("Whole recording", None)
            for region in self.owner.project.regions:
                self.region.addItem(region.name, region.id)
            if saved is not None and self.region.findData(saved) < 0:
                self.region.addItem("Missing saved region", saved)
            self.region.setCurrentIndex(max(0, self.region.findData(saved)))

    def restore(self):
        self.restoring = True
        try:
            parameters = self.owner.project.stages["eq"].parameters
            rate = self.owner.source[1] if self.owner.source else 48000
            try:
                settings = eq_from_parameters(parameters)
                validate_eq(settings, rate, self.owner.project.regions)
            except (TypeError, ValueError) as error:
                settings = EQSettings()
                self.summary.setText(str(error))
            self.bands = list(settings.bands)
            self.refresh_regions()
            with QtCore.QSignalBlocker(self.region):
                index = self.region.findData(settings.region_id)
                if settings.region_id is not None and index < 0:
                    self.region.addItem("Missing saved region", settings.region_id)
                    index = self.region.count() - 1
                self.region.setCurrentIndex(max(0, index))
            with QtCore.QSignalBlocker(self.transition):
                self.transition.setValue(settings.transition_ms)
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(not self.owner.project.stages["eq"].bypassed)
            with QtCore.QSignalBlocker(self.frequency):
                self.frequency.setRange(20, min(20000, rate / 2 - 1))
            self.selected = 0 if self.bands else -1
            self.refresh_list()
        finally:
            self.restoring = False
        self.draw_response()

    def refresh_list(self):
        with QtCore.QSignalBlocker(self.band_list):
            self.band_list.clear()
            for index, band in enumerate(self.bands):
                title = f"{index + 1}. {band.frequency:g} Hz  {band.gain_db:+.1f} dB"
                self.band_list.addItem(title if band.enabled else title + " (off)")
            self.band_list.setCurrentRow(self.selected)
        self.select_band(self.selected)
        self.add_button.setEnabled(len(self.bands) < MAX_BANDS)

    def select_band(self, index):
        self.selected = index if 0 <= index < len(self.bands) else -1
        band = self.bands[self.selected] if self.selected >= 0 else BellSettings()
        for control, value in (
            (self.frequency, band.frequency),
            (self.gain, band.gain_db),
            (self.q, band.q),
        ):
            with QtCore.QSignalBlocker(control):
                control.setValue(value)
            control.setEnabled(self.selected >= 0)
        with QtCore.QSignalBlocker(self.band_enabled):
            self.band_enabled.setChecked(band.enabled)
        for control in (
            self.band_enabled,
            self.duplicate_button,
            self.remove_button,
            self.reset_button,
        ):
            control.setEnabled(self.selected >= 0)
        self.draw_response()

    def add_band(self, frequency=2400, gain=0):
        if len(self.bands) >= MAX_BANDS:
            return
        rate = self.owner.source[1] if self.owner.source else 48000
        self.bands.append(
            BellSettings(frequency=min(float(frequency), rate / 2 - 1), gain_db=float(gain))
        )
        self.selected = len(self.bands) - 1
        self.enabled.setChecked(True)
        self.refresh_list()
        self.changed()

    def duplicate_band(self):
        if self.selected >= 0 and len(self.bands) < MAX_BANDS:
            band = self.bands[self.selected]
            self.add_band(band.frequency, band.gain_db)
            self.bands[-1] = band
            self.refresh_list()
            self.changed()

    def remove_band(self):
        if self.selected >= 0:
            self.bands.pop(self.selected)
            self.selected = min(self.selected, len(self.bands) - 1)
            self.refresh_list()
            self.changed()

    def edit_band(self, *args):
        if self.restoring or self.selected < 0 or self.owner.restoring_project:
            return
        self.bands[self.selected] = replace(
            self.bands[self.selected],
            frequency=self.frequency.value(),
            gain_db=self.gain.value(),
            q=self.q.value(),
            enabled=self.band_enabled.isChecked(),
        )
        self.refresh_list()
        self.changed()

    def move_band(self, index, frequency, gain):
        if not 0 <= index < len(self.bands):
            return
        self.selected = index
        self.bands[index] = replace(self.bands[index], frequency=frequency, gain_db=gain)
        self.refresh_list()
        self.draw_response()

    def change_width(self, index, q):
        if 0 <= index < len(self.bands):
            self.bands[index] = replace(self.bands[index], q=q)
            self.selected = index
            self.refresh_list()
            self.changed()

    def changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        settings = self.settings()
        self.owner.project.stages["eq"].parameters = eq_parameters(settings)
        self.owner.project.stages["eq"].bypassed = not self.enabled.isChecked()
        self.owner.invalidate_eq()
        self.draw_response()
        region = next(
            (item for item in self.owner.project.regions if item.id == settings.region_id), None
        )
        if region is not None:
            self.owner.waveform.set_selection(region.bounds)
        self.summary.setText(
            "EQ edited. Matching and repairs are retained. Render before comparing."
        )

    def draw_response(self):
        if self.owner.source is None:
            return
        settings = self.settings()
        bypassed = self.owner.project.stages["eq"].bypassed
        scope = self.region.currentText()
        self.canvas.set_settings(settings, self.owner.source[1], self.selected, bypassed, scope)
        region = next(
            (item.bounds for item in self.owner.project.regions if item.id == settings.region_id),
            None,
        )
        active = any(band.enabled and band.gain_db != 0 for band in settings.bands)
        self.owner.waveform.set_eq_scope(
            None if bypassed or not active else region,
            settings.transition_ms,
            self.bands[self.selected].gain_db if self.selected >= 0 else 0,
            self.bands[self.selected].frequency if self.selected >= 0 else 2400,
        )

    def listen(self):
        if self.owner.eq_preview is None:
            return
        settings = self.settings()
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
