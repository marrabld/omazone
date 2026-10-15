"""Whole-recording corrective-filter inspector, separate from region-scoped EQ."""

import math
from dataclasses import replace

from PySide6 import QtCore, QtWidgets

from .filter_canvas import FilterCanvas
from .filters import (
    MAX_FILTERS,
    SLOPES,
    FilterSettings,
    FiltersSettings,
    filter_parameters,
    filters_from_parameters,
    validate_settings,
)

KINDS = (("High-pass", "highpass"), ("Low-pass", "lowpass"), ("Notch", "notch"))


class FilterView(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.restoring = False
        self.items = []
        self.selected = -1
        self.invalid_recipe = None
        self.canvas = FilterCanvas(type(owner.spectrum_plot.getAxis("bottom")))
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.back_button = QtWidgets.QPushButton("Back to bell EQ")
        self.back_button.clicked.connect(lambda: owner.views.setStep("eq"))
        layout.addWidget(self.back_button)
        prompt = QtWidgets.QLabel(
            "Whole-recording filters, after bell EQ and before dynamics. "
            "Choose a type and add a filter. Drag a line for frequency; "
            "scroll for slope or notch Q. Passage selection affects audition only."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        self.filter_list = QtWidgets.QListWidget()
        self.filter_list.setMaximumHeight(95)
        layout.addWidget(self.filter_list)
        row = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("Add filter")
        self.duplicate_button = QtWidgets.QPushButton("Duplicate")
        self.remove_button = QtWidgets.QPushButton("Remove")
        for button in (self.add_button, self.duplicate_button, self.remove_button):
            row.addWidget(button)
        layout.addLayout(row)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        self.kind = QtWidgets.QComboBox()
        for title, key in KINDS:
            self.kind.addItem(title, key)
        self.frequency = QtWidgets.QDoubleSpinBox()
        self.frequency.setDecimals(3)
        self.frequency.setSuffix(" Hz")
        self.slope = QtWidgets.QComboBox()
        for slope in SLOPES:
            self.slope.addItem(f"{slope} dB/oct", slope)
        self.q = QtWidgets.QDoubleSpinBox()
        self.q.setDecimals(3)
        self.q.setRange(0.1, 100)
        for title, control in (("Type", self.kind), ("Frequency", self.frequency),
                               ("Slope", self.slope), ("Notch Q", self.q)):
            label = QtWidgets.QLabel(title)
            label.setProperty("formLabel", True)
            # The window makes inspector labels horizontally Ignored so prose
            # can wrap. Form labels still need a width beside their controls.
            label.setMinimumWidth(label.fontMetrics().horizontalAdvance(title) + 12)
            form.addRow(label, control)
        self.filter_enabled = QtWidgets.QCheckBox("Apply selected filter")
        self.enabled = QtWidgets.QCheckBox("Apply corrective filters")
        layout.addWidget(self.filter_enabled)
        layout.addWidget(self.enabled)
        note = QtWidgets.QLabel(
            "Causal IIR phase; no buffering delay. High-Q notches can ring. Render to apply changes."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.listen_button = QtWidgets.QPushButton("Listen and compare filters")
        self.listen_button.clicked.connect(self.listen)
        layout.addWidget(self.listen_button)
        self.clear_button = QtWidgets.QPushButton("Clear all filters")
        self.clear_button.clicked.connect(self.clear_filters)
        layout.addWidget(self.clear_button)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.render_button = QtWidgets.QPushButton("Render filters")
        self.render_button.clicked.connect(owner.render_saved_recipe)
        self.filter_list.currentRowChanged.connect(self.select_filter)
        self.add_button.clicked.connect(lambda: self.add_filter())
        self.duplicate_button.clicked.connect(self.duplicate_filter)
        self.remove_button.clicked.connect(self.remove_filter)
        self.kind.currentIndexChanged.connect(lambda: self.edit_filter(kind=self.kind.currentData()))
        self.frequency.valueChanged.connect(lambda value: self.edit_filter(frequency=value))
        self.slope.currentIndexChanged.connect(lambda: self.edit_filter(slope=self.slope.currentData()))
        self.q.valueChanged.connect(lambda value: self.edit_filter(q=value))
        self.filter_enabled.toggled.connect(lambda value: self.edit_filter(enabled=value))
        self.enabled.toggled.connect(self.toggle_enabled)
        self.canvas.placed.connect(self.add_filter)
        self.canvas.selected.connect(self.filter_list.setCurrentRow)
        self.canvas.moved.connect(self.move_filter)
        self.canvas.adjusted.connect(self.adjust_filter)
        self.restore()

    @property
    def rate(self):
        return self.owner.source[1] if self.owner.source else 48000

    def settings(self):
        return FiltersSettings(tuple(self.items))

    def q_minimum(self, frequency):
        return max(0.1, (math.floor(2 * frequency / self.rate * 1000) + 1) / 1000)

    def safe_notch(self, item):
        if item.kind == "notch" and item.frequency / item.q >= self.rate / 2:
            return replace(item, q=self.q_minimum(item.frequency))
        return item

    def restore(self):
        self.restoring = True
        try:
            self.invalid_recipe = None
            try:
                settings = filters_from_parameters(self.owner.project.stages["filters"].parameters)
                validate_settings(settings, self.rate)
            except (TypeError, ValueError) as error:
                self.invalid_recipe = str(error)
                settings = FiltersSettings()
            self.items = list(settings.filters)
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(not self.owner.project.stages["filters"].bypassed)
            self.selected = 0 if self.items else -1
            self.refresh_list()
        finally:
            self.restoring = False
        self.draw_response()
        self.summary.setText(self.invalid_recipe or self.owner.workflow_status().stages["filters"].reason)

    def refresh_list(self):
        with QtCore.QSignalBlocker(self.filter_list):
            self.filter_list.clear()
            for index, item in enumerate(self.items):
                title = dict((key, title) for title, key in KINDS)[item.kind]
                detail = f"Q {item.q:g}" if item.kind == "notch" else f"{item.slope} dB/oct"
                self.filter_list.addItem(
                    f"{index + 1}. {title}  {item.frequency:g} Hz  {detail}"
                    + (" (off)" if not item.enabled else "")
                )
            self.filter_list.setCurrentRow(self.selected)
        self.select_filter(self.selected)
        self.add_button.setEnabled(self.invalid_recipe is None and len(self.items) < MAX_FILTERS)

    def select_filter(self, index):
        self.selected = index if 0 <= index < len(self.items) else -1
        item = self.items[self.selected] if self.selected >= 0 else FilterSettings()
        with QtCore.QSignalBlocker(self.frequency), QtCore.QSignalBlocker(self.q):
            self.frequency.setRange(20, self.rate / 2 - 0.001)
            self.q.setMinimum(self.q_minimum(item.frequency) if item.kind == "notch" else 0.1)
            self.frequency.setValue(item.frequency)
            self.q.setValue(item.q)
        for control, index in ((self.kind, self.kind.findData(item.kind)), (self.slope, self.slope.findData(item.slope))):
            with QtCore.QSignalBlocker(control):
                control.setCurrentIndex(index)
        with QtCore.QSignalBlocker(self.filter_enabled):
            self.filter_enabled.setChecked(item.enabled)
        active = self.selected >= 0 and self.invalid_recipe is None
        for control in (self.frequency, self.filter_enabled, self.remove_button):
            control.setEnabled(active)
        self.kind.setEnabled(self.invalid_recipe is None)
        self.slope.setEnabled(active and item.kind != "notch")
        self.q.setEnabled(active and item.kind == "notch")
        self.duplicate_button.setEnabled(active and len(self.items) < MAX_FILTERS)
        self.draw_response()

    def add_filter(self, frequency=None):
        if self.invalid_recipe or len(self.items) >= MAX_FILTERS:
            return
        kind = self.kind.currentData()
        default = {"highpass": 30, "lowpass": min(18000, self.rate / 3), "notch": 50}[kind]
        item = FilterSettings(kind=kind, frequency=min(max(20, frequency or default), self.rate / 2 - 0.001), q=30 if kind == "notch" else 1)
        self.items.append(self.safe_notch(item))
        self.selected = len(self.items) - 1
        with QtCore.QSignalBlocker(self.enabled):
            self.enabled.setChecked(True)
        self.changed()

    def duplicate_filter(self):
        if self.selected >= 0 and len(self.items) < MAX_FILTERS:
            self.items.append(self.items[self.selected])
            self.selected = len(self.items) - 1
            self.changed()

    def remove_filter(self):
        if self.selected >= 0:
            self.items.pop(self.selected)
            self.selected = min(self.selected, len(self.items) - 1)
            self.changed()

    def clear_filters(self):
        self.invalid_recipe = None
        self.items = []
        self.selected = -1
        with QtCore.QSignalBlocker(self.enabled):
            self.enabled.setChecked(False)
        self.changed()

    def edit_filter(self, **changes):
        if self.restoring or self.owner.restoring_project or self.selected < 0 or self.invalid_recipe:
            return
        item = replace(self.items[self.selected], **changes)
        safe = self.safe_notch(item)
        self.items[self.selected] = safe
        self.changed()
        if safe.q != item.q:
            self.summary.setText(f"Notch Q raised to {safe.q:g} to keep bandwidth below Nyquist. Render to apply.")

    def move_filter(self, index, frequency):
        if 0 <= index < len(self.items):
            self.selected = index
            self.edit_filter(frequency=min(max(frequency, 20), self.rate / 2 - 0.001))

    def adjust_filter(self, index, steps):
        if not 0 <= index < len(self.items) or steps == 0:
            return
        self.selected = index
        item = self.items[index]
        if item.kind == "notch":
            self.edit_filter(q=min(100, max(self.q_minimum(item.frequency), item.q * 1.1 ** steps)))
        else:
            slope = SLOPES[min(3, max(0, SLOPES.index(item.slope) + (1 if steps > 0 else -1)))]
            self.edit_filter(slope=slope)

    def toggle_enabled(self, enabled):
        if not self.restoring and not self.owner.restoring_project:
            self.owner.set_stage_bypass("filters", not enabled)

    def changed(self):
        validate_settings(self.settings(), self.rate)
        stage = self.owner.project.stages["filters"]
        stage.parameters = filter_parameters(self.settings())
        stage.bypassed = not self.enabled.isChecked()
        self.owner.invalidate_filters()
        self.refresh_list()
        self.summary.setText(self.owner.workflow_status().stages["filters"].reason)

    def draw_response(self):
        self.canvas.set_settings(
            self.settings(), self.rate, self.selected,
            self.owner.project.stages["filters"].bypassed or self.invalid_recipe is not None,
        )

    def listen(self):
        if not self.owner.comparison("filters").available:
            return
        self.owner.apply_comparison("filters")
        if self.owner.waveform.selection:
            self.owner.play_selection()
        else:
            self.owner.play()
