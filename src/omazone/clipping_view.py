"""Selected-original-audio diagnostics and navigation of suspected clipping."""

from heapq import nsmallest
from itertools import chain

import numpy as np
from PySide6 import QtCore, QtWidgets

from .clipping import DetectionSettings, detect_clipping, suggest_thresholds


class ClippingInspector(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.report = None
        self.rows = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        description = QtWidgets.QLabel(
            "Select a passage in Waveform / selection, then analyse the original audio. "
            "Red: suspected flat peaks. Orange: samples above full scale. "
            "This detects candidates; it does not repair audio or isolate a clipped instrument from a mix."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.scope = QtWidgets.QLabel("Select a mix passage first.")
        layout.addWidget(self.scope)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("Channel"))
        self.channel = QtWidgets.QComboBox()
        self.channel.addItem("All", None)
        controls.addWidget(self.channel)
        self.positive = self.spin(controls, "+ threshold", 0.000001, 100, 1.0, 6)
        self.negative = self.spin(controls, "- threshold", -100, -0.000001, -1.0, 6)
        self.tolerance = self.spin(controls, "Tolerance", 0.000001, 0.1, 0.00005, 6)
        controls.addWidget(QtWidgets.QLabel("Min run"))
        self.minimum_run = QtWidgets.QSpinBox()
        self.minimum_run.setRange(2, 10000)
        self.minimum_run.setValue(3)
        self.minimum_run.setSuffix(" samples")
        controls.addWidget(self.minimum_run)
        layout.addLayout(controls)
        row = QtWidgets.QHBoxLayout()
        self.analyse_button = self.button(row, "Analyse selection", self.analyse)
        self.suggest_button = self.button(row, "Suggest levels", self.suggest)
        self.button(row, "Show interval", self.show_interval)
        self.button(row, "Show selection", self.show_selection)
        self.button(row, "Clear markers", self.clear)
        layout.addLayout(row)
        self.summary = QtWidgets.QLabel(
            "No analysis yet. Detection does not change the mix or export."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("Channel", "Type", "Start (s)", "End (s)", "Samples", "Level")
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda *args: self.show_interval())
        layout.addWidget(self.table, 1)
        self.hint = QtWidgets.QLabel(
            "Thresholds are linear sample amplitudes, not dB. Defaults include near-full-scale PCM rails. "
            "Suggestions look for flat runs and can mistake clean extrema or synthesised signals for clipping. "
            "For mixed-in distortion, inspect the isolated recording when available."
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        for control in (self.positive, self.negative, self.tolerance, self.minimum_run):
            control.valueChanged.connect(self.controls_changed)
        self.channel.currentIndexChanged.connect(self.controls_changed)

    def spin(self, row, name, low, high, value, decimals):
        row.addWidget(QtWidgets.QLabel(name))
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(low, high)
        control.setDecimals(decimals)
        control.setValue(value)
        control.setSingleStep(0.00001 if name == "Tolerance" else 0.01)
        row.addWidget(control)
        return control

    def button(self, row, text, callback):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def settings(self):
        return DetectionSettings(
            self.positive.value(),
            self.negative.value(),
            self.tolerance.value(),
            self.minimum_run.value(),
            self.channel.currentData(),
        )

    def reset_source(self):
        self.clear()
        with QtCore.QSignalBlocker(self.channel):
            self.channel.clear()
            if self.owner.source[0].shape[1] == 1:
                self.channel.addItem("Mono", 0)
            else:
                self.channel.addItem("Both", None)
                self.channel.addItem("Left", 0)
                self.channel.addItem("Right", 1)
        for control, value in (
            (self.positive, 1),
            (self.negative, -1),
            (self.tolerance, 0.00005),
            (self.minimum_run, 3),
        ):
            with QtCore.QSignalBlocker(control):
                control.setValue(value)
        self.selection_changed(self.owner.waveform.selection)

    def selection_changed(self, region):
        if self.report is not None and region != self.report.region:
            self.clear()
        if region is None or self.owner.source is None:
            self.scope.setText("Select a mix passage first.")
        else:
            rate = self.owner.source[1]
            self.scope.setText(
                f"Original mix | {region.start / rate:.6f}-{region.end / rate:.6f} s | Samples [{region.start}, {region.end})"
            )

    def controls_changed(self, *args):
        self.clear()
        self.summary.setText("Detection controls changed. Analyse selection to refresh candidates.")

    def clear(self):
        self.report = None
        self.rows = []
        self.table.setRowCount(0)
        self.owner.waveform.set_clipping(None)
        self.summary.setText("No analysis yet. Detection does not change the mix or export.")

    def inputs(self):
        if self.owner.source is None or self.owner.waveform.selection is None:
            self.owner.error("Load a mix and select a passage first.")
            return None
        return self.owner.source[0], self.owner.waveform.selection, self.settings()

    def analyse(self):
        inputs = self.inputs()
        if inputs is not None:
            self.owner.start_job(
                lambda: detect_clipping(*inputs),
                self.analysed,
                "Scanning selected original audio for suspected hard clipping…",
            )

    def analysed(self, report):
        self.report = report
        self.owner.waveform.set_clipping(report)
        count = len(report.candidates)
        details = []
        for stats in report.stats:
            peak_db = 20 * np.log10(max(stats.peak, 1e-12))
            details.append(
                f"{self.channel_name(stats.channel)}: peak {peak_db:.1f} dBFS, {stats.candidate_samples} candidate samples, "
                f"{stats.above_full_scale_samples} above full scale, {stats.near_full_scale_samples} at/near full scale"
            )
        self.summary.setText(f"{count} suspected plateau runs | {' | '.join(details)}")
        entries = chain(
            ((item, "Suspected " + item.polarity + " plateau") for item in report.candidates),
            ((item, "Above full scale") for item in report.overloads),
        )
        self.rows = nsmallest(500, entries, key=lambda entry: (entry[0].start, entry[0].channel))
        self.table.setRowCount(len(self.rows))
        rate = self.owner.source[1]
        for row, (interval, kind) in enumerate(self.rows):
            values = (
                self.channel_name(interval.channel),
                kind,
                f"{interval.start / rate:.6f}",
                f"{interval.end / rate:.6f}",
                str(interval.end - interval.start),
                f"{interval.level:.6f}",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        entry_count = len(report.candidates) + len(report.overloads)
        if entry_count > 500:
            self.summary.setText(
                self.summary.text()
                + f" | Table shows first 500 of {entry_count} intervals; totals include all."
            )
        self.owner.status.setText(
            "Clipping analysis complete. Show an interval to inspect its samples; candidates are not a repair mask yet."
        )

    def channel_name(self, channel):
        return "Mono" if self.owner.source[0].shape[1] == 1 else ("Left", "Right")[channel]

    def suggest(self):
        inputs = self.inputs()
        if inputs is not None:
            self.owner.start_job(
                lambda: suggest_thresholds(*inputs),
                self.suggested,
                "Looking for plateau-level evidence…",
            )

    def suggested(self, hints):
        messages = []
        for name, control in (("positive", self.positive), ("negative", self.negative)):
            values = [getattr(hint, name) for hint in hints if getattr(hint, name) is not None]
            if not values:
                messages.append(f"No supported {name} plateau level")
            elif max(values) - min(values) > self.tolerance.value():
                messages.append(
                    f"{name.capitalize()} levels differ by channel; choose Left/Right and suggest separately"
                )
            else:
                control.setValue(float(np.median(values)))
                messages.append(f"Suggested {name} level {control.value():.6f}")
        self.summary.setText(
            ". ".join(messages)
            + ". Review and Analyse selection; these are hints, not confirmed clipping thresholds."
        )

    def show_selection(self):
        if self.owner.waveform.selection is not None:
            self.owner.waveform.zoom_selection()
            self.owner.views.setCurrentWidget(self.owner.waveform)

    def show_interval(self):
        row = self.table.currentRow()
        if not self.table.selectedItems() or not 0 <= row < len(self.rows):
            return
        interval = self.rows[row][0]
        rate = self.owner.source[1]
        padding = max(16, round(rate * 0.001))
        self.owner.waveform.channel_plots[0].setXRange(
            max(0, interval.start - padding) / rate,
            min(len(self.owner.source[0]), interval.end + padding) / rate,
            padding=0,
        )
        self.owner.seek(interval.start)
        self.owner.views.setCurrentWidget(self.owner.waveform)
