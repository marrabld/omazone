"""Selected-original-audio diagnostics and navigation of suspected clipping."""

from dataclasses import replace
from heapq import nsmallest
from itertools import chain
from uuid import uuid4

import numpy as np
from PySide6 import QtCore, QtWidgets

from .clipping import DetectionSettings, detect_clipping, find_clipping, suggest_thresholds
from .declipping import RepairSettings, repair_clipping
from .engine import analyse, audition_pair
from .project import RepairOperation, replay_repairs


class ClippingInspector(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.report = None
        self.rows = []
        self.last_attempt = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        description = QtWidgets.QLabel(
            "Find possible damage in your selected passage, review the peaks, then compare a repair with the original."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.scope = QtWidgets.QLabel("Select a mix passage first.")
        layout.addWidget(self.scope)
        primary = QtWidgets.QHBoxLayout()
        self.analyse_button = self.button(primary, "1. Find clipped peaks", self.find_peaks)
        self.analyse_button.setStyleSheet(
            "QPushButton { background: #63dfc0; color: #10151e; font-weight: bold; } QPushButton:disabled { background: #253246; color: #647085; }"
        )
        self.review_button = self.button(primary, "2. Review peaks", self.toggle_review)
        self.repair_button = self.button(primary, "3. Try repair", self.repair)
        layout.addLayout(primary)
        self.result_heading = QtWidgets.QLabel("Ready to scan")
        self.result_heading.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self.result_heading)
        self.summary = QtWidgets.QLabel(
            "Scanning leaves your recording unchanged. Both channels are handled automatically."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.repair_summary = QtWidgets.QLabel("Original audio is unchanged.")
        self.repair_summary.setWordWrap(True)
        self.repair_summary.hide()
        layout.addWidget(self.repair_summary)
        self.result_actions = QtWidgets.QWidget()
        repair_actions = QtWidgets.QHBoxLayout(self.result_actions)
        repair_actions.setContentsMargins(0, 0, 0, 0)
        self.listen_button = self.button(repair_actions, "Listen to repair", self.listen)
        self.reset_repair_button = self.button(
            repair_actions, "Undo repair", self.owner.reset_repair
        )
        self.export_repair_button = self.button(
            repair_actions, "Save repaired audio", self.owner.export_repair
        )
        self.result_actions.hide()
        layout.addWidget(self.result_actions)

        self.review_panel = QtWidgets.QGroupBox("Choose peaks to try repairing")
        review_layout = QtWidgets.QVBoxLayout(self.review_panel)
        review_layout.addWidget(
            QtWidgets.QLabel(
                "Only checked peaks will be changed. Inspect a peak in the waveform if unsure."
            )
        )
        review_actions = QtWidgets.QHBoxLayout()
        self.button(review_actions, "Inspect selected peak", self.show_interval)
        self.button(review_actions, "Include shown peaks", lambda: self.check_shown(True))
        self.button(review_actions, "Exclude shown peaks", lambda: self.check_shown(False))
        review_layout.addLayout(review_actions)
        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda *args: self.show_interval())
        self.table.itemChanged.connect(lambda *args: self.owner.update_buttons())
        review_layout.addWidget(self.table)
        self.review_panel.hide()
        layout.addWidget(self.review_panel)

        self.advanced_toggle = QtWidgets.QToolButton()
        self.advanced_toggle.setText("Advanced settings and measurements")
        self.advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.advanced_toggle.toggled.connect(self.toggle_advanced)
        layout.addWidget(self.advanced_toggle)
        self.advanced_panel = QtWidgets.QWidget()
        advanced = QtWidgets.QVBoxLayout(self.advanced_panel)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.scope_details = QtWidgets.QLabel()
        advanced.addWidget(self.scope_details)
        manual_note = QtWidgets.QLabel(
            "Manual scan settings. The primary Find button estimates separate levels for each channel."
        )
        manual_note.setWordWrap(True)
        advanced.addWidget(manual_note)
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
        advanced.addLayout(controls)
        row = QtWidgets.QHBoxLayout()
        self.manual_analyse_button = self.button(row, "Scan with these settings", self.analyse)
        self.suggest_button = self.button(row, "Suggest levels", self.suggest)
        self.button(row, "Show selection", self.show_selection)
        self.button(row, "Clear markers", self.clear)
        advanced.addLayout(row)
        repair_controls = QtWidgets.QHBoxLayout()
        self.max_run_ms = self.spin(repair_controls, "Max gap (ms)", 0.01, 10, 1, 2)
        repair_controls.addWidget(QtWidgets.QLabel("Context / side"))
        self.context_samples = QtWidgets.QSpinBox()
        self.context_samples.setRange(3, 64)
        self.context_samples.setValue(8)
        self.context_samples.setSuffix(" samples")
        repair_controls.addWidget(self.context_samples)
        self.max_peak_ratio = self.spin(repair_controls, "Peak bound (x rail)", 1, 10, 4, 1)
        advanced.addLayout(repair_controls)
        self.detailed_summary = QtWidgets.QLabel()
        self.detailed_summary.setWordWrap(True)
        advanced.addWidget(self.detailed_summary)
        self.detailed_repair_summary = QtWidgets.QLabel()
        self.detailed_repair_summary.setWordWrap(True)
        advanced.addWidget(self.detailed_repair_summary)
        self.hint = QtWidgets.QLabel(
            "Thresholds are linear sample amplitudes, not dB. Defaults include near-full-scale PCM rails. "
            "Suggestions look for flat runs and can mistake clean extrema or synthesised signals for clipping. "
            "For mixed-in distortion, inspect the isolated recording when available."
            " Repair checked replaces the current repair preview using the original recording."
        )
        self.hint.setWordWrap(True)
        advanced.addWidget(self.hint)
        self.advanced_panel.hide()
        layout.addWidget(self.advanced_panel)
        layout.addStretch(1)
        for control in (self.positive, self.negative, self.tolerance, self.minimum_run):
            control.valueChanged.connect(self.controls_changed)
        self.channel.currentIndexChanged.connect(self.controls_changed)
        self.update_table_view()

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

    def toggle_review(self):
        self.review_panel.setVisible(self.review_panel.isHidden())

    def toggle_advanced(self, expanded):
        self.advanced_panel.setVisible(expanded)
        self.advanced_toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow
        )
        if expanded and self.rows:
            self.review_panel.show()
        self.update_table_view()
        self.owner.tab_changed()

    def update_table_view(self):
        advanced = self.advanced_toggle.isChecked()
        self.table.setHorizontalHeaderLabels(
            (
                "Include",
                "Channel",
                "Type",
                "Start (s)" if advanced else "Position",
                "End (s)",
                "Samples",
                "Level",
                "Result",
            )
        )
        for column in range(8):
            self.table.setColumnHidden(column, not advanced and column not in (0, 1, 3, 7))
        self.table.setColumnHidden(7, not advanced and self.last_attempt is None)
        self.table.setMaximumHeight(16777215 if advanced else 240)
        for row, (interval, kind) in enumerate(self.rows):
            self.table.setRowHidden(row, not advanced and not kind.startswith("Suspected"))
            self.table.item(row, 3).setText(
                f"{interval.start / self.owner.source[1]:.6f}"
                if advanced
                else self.timestamp(interval.start, 3)
            )
            result = self.table.item(row, 7)
            full = result.data(QtCore.Qt.ItemDataRole.UserRole) or ""
            result.setText(
                full if advanced or not full.startswith("Skipped:") else "Kept unchanged"
            )

    def timestamp(self, samples, precision=1):
        scale = 10**precision
        minutes, remainder = divmod(round(samples / self.owner.source[1] * scale), 60 * scale)
        seconds, fraction = divmod(remainder, scale)
        return f"{minutes}:{seconds:02d}.{fraction:0{precision}d}"

    def refresh_actions(self):
        active = self.owner.repair_result is not None
        self.result_actions.setVisible(active)
        self.repair_summary.setVisible(active or self.last_attempt is not None)
        if active and self.last_attempt is None:
            self.repair_summary.setText(
                "A repair preview is available. Listen to it or undo it below."
            )
        next_action = self.analyse_button
        if self.report is not None and self.report.candidates:
            next_action = self.repair_button if self.checked_intervals() else self.review_button
        if active and self.last_attempt is not None and self.last_attempt.repaired:
            next_action = self.listen_button
        for button in (
            self.analyse_button,
            self.review_button,
            self.repair_button,
            self.listen_button,
        ):
            emphasized = button is next_action
            if button.property("nextAction") != emphasized:
                button.setProperty("nextAction", emphasized)
                button.setStyleSheet(
                    "QPushButton { background: #63dfc0; color: #10151e; font-weight: bold; } QPushButton:disabled { background: #253246; color: #647085; }"
                    if emphasized
                    else ""
                )

    def reset_repair_status(self):
        self.last_attempt = None
        self.repair_summary.setText("Original audio is unchanged.")
        self.repair_summary.hide()
        self.detailed_repair_summary.clear()
        if self.report is not None and self.report.candidates:
            self.result_heading.setText(
                f"{len(self.report.candidates)} possible clipped peaks found"
            )
            self.summary.setText("Original input restored. Choose peaks to try a new repair.")
        else:
            self.result_heading.setText("Ready to scan")
        self.update_table_view()

    def listen(self):
        if self.owner.repair_preview is not None and self.owner.waveform.selection is not None:
            self.owner.preview_mode.setCurrentIndex(1)
            self.owner.loop_selection.setChecked(True)
            self.owner.play_selection()

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
        self.advanced_toggle.setChecked(False)
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
        self.reset_repair_status()

    def selection_changed(self, region):
        if self.report is not None and region != self.report.region:
            self.clear()
        if region is None or self.owner.source is None:
            self.scope.setText("Select a mix passage first.")
            self.scope_details.clear()
        else:
            rate = self.owner.source[1]
            duration = (region.end - region.start) / rate
            precision = 3 if duration < 1 else 1

            self.scope.setText(
                f"Selected passage: {self.timestamp(region.start, precision)} to {self.timestamp(region.end, precision)} ({duration:.{precision}f} seconds)"
            )
            self.scope_details.setText(
                f"Original recording | Samples [{region.start}, {region.end}) | {rate} Hz"
            )

    def controls_changed(self, *args):
        self.clear()
        self.summary.setText(
            "Settings changed. Find peaks again, or run a manual scan under Advanced."
        )

    def clear(self):
        self.report = None
        self.rows = []
        self.table.setRowCount(0)
        self.owner.waveform.set_clipping(None)
        self.review_panel.hide()
        self.result_heading.setText("Ready to scan")
        self.summary.setText(
            "Scanning leaves your recording unchanged. Both channels are handled automatically."
        )
        self.detailed_summary.clear()
        self.last_attempt = None
        self.update_table_view()
        self.owner.update_buttons()

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

    def find_peaks(self):
        inputs = self.inputs()
        if inputs is None:
            return
        original, region, _ = inputs
        settings = DetectionSettings(
            tolerance=self.tolerance.value(), minimum_run=self.minimum_run.value()
        )
        self.clear()
        self.result_heading.setText("Finding possible clipped peaks…")
        self.summary.setText("Checking each channel separately. Your audio is unchanged.")
        self.owner.start_job(
            lambda: find_clipping(original, region, settings),
            self.analysed,
            "Finding possible clipped peaks…",
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
        self.detailed_summary.setText(f"{count} suspected plateau runs | {' | '.join(details)}")
        rails = [
            f"{self.channel_name(item.channel)} rails: +{report.settings_for(item.channel).positive:.6f} / {report.settings_for(item.channel).negative:.6f}"
            for item in report.stats
        ]
        self.detailed_summary.setText(self.detailed_summary.text() + " | " + " | ".join(rails))
        if count:
            self.result_heading.setText(f"{count} possible clipped peaks found")
            self.summary.setText(
                "Review the marked peaks, then choose which ones to try repairing."
            )
        elif report.overloads:
            self.result_heading.setText("Above-full-scale audio found")
            self.summary.setText(
                "This may be a volume-level problem rather than flattened peaks. No flat peaks were found to reconstruct."
            )
        else:
            self.result_heading.setText("No clear clipped peaks found")
            self.summary.setText(
                "A mixed track can hide clipping in one instrument. Try its isolated recording when available; absence of markers does not prove it is clean."
            )
        entries = chain(
            ((item, "Suspected " + item.polarity + " plateau") for item in report.candidates),
            ((item, "Above full scale") for item in report.overloads),
        )
        self.rows = nsmallest(500, entries, key=lambda entry: (entry[0].start, entry[0].channel))
        blocker = QtCore.QSignalBlocker(self.table)
        self.table.setRowCount(len(self.rows))
        rate = self.owner.source[1]
        for row, (interval, kind) in enumerate(self.rows):
            checkbox = QtWidgets.QTableWidgetItem()
            checkbox.setToolTip(
                f"{kind} | Samples [{interval.start}, {interval.end}) | Level {interval.level:.6f}"
            )
            if kind.startswith("Suspected"):
                checkbox.setFlags(checkbox.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                checkbox.setCheckState(QtCore.Qt.CheckState.Unchecked)
                checkbox.setToolTip("Include this plateau interval in the next reconstruction.")
            else:
                checkbox.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
                )
                checkbox.setToolTip("Over-range samples alone are not eligible for repair.")
            self.table.setItem(row, 0, checkbox)
            values = (
                self.channel_name(interval.channel),
                kind,
                f"{interval.start / rate:.6f}",
                f"{interval.end / rate:.6f}",
                str(interval.end - interval.start),
                f"{interval.level:.6f}",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column + 1, QtWidgets.QTableWidgetItem(value))
            self.table.setItem(row, 7, QtWidgets.QTableWidgetItem(""))
        del blocker
        entry_count = len(report.candidates) + len(report.overloads)
        if entry_count > 500:
            self.detailed_summary.setText(
                self.detailed_summary.text()
                + f" | Table shows first 500 of {entry_count} intervals; totals include all."
            )
        self.owner.status.setText("Scan complete. Review possible peaks before trying a repair.")
        self.last_attempt = None
        self.update_table_view()
        self.owner.update_buttons()

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
        self.detailed_summary.setText(
            ". ".join(messages)
            + ". Review and Analyse selection; these are hints, not confirmed clipping thresholds."
        )
        self.summary.setText(
            "Level suggestions are shown under Advanced. Use Scan with these settings to inspect them."
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

    def checked_intervals(self):
        return tuple(
            interval
            for row, (interval, kind) in enumerate(self.rows)
            if kind.startswith("Suspected")
            and self.table.item(row, 0).checkState() == QtCore.Qt.CheckState.Checked
        )

    def check_shown(self, checked):
        with QtCore.QSignalBlocker(self.table):
            for row, (_, kind) in enumerate(self.rows):
                if kind.startswith("Suspected"):
                    self.table.item(row, 0).setCheckState(
                        QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
                    )
        self.owner.update_buttons()

    def repair(self):
        if self.report is None or not self.checked_intervals():
            self.owner.error("Analyse a selection and check at least one plateau candidate first.")
            return
        original, rate = self.owner.source[:2]
        report = self.report
        accepted = self.checked_intervals()
        settings = RepairSettings(
            self.max_run_ms.value(), self.context_samples.value(), self.max_peak_ratio.value()
        )

        def calculate():
            result = repair_clipping(original, rate, report, accepted, settings)
            if not result.repaired:
                return result, None, None
            operation = RepairOperation(
                uuid4().hex,
                report.region,
                report.settings,
                report.channel_settings,
                result.repaired,
                settings,
            )
            operations = self.owner.add_repair_recipe(operation)
            combined = replay_repairs(original, rate, operations)
            combined = replace(combined, rejected=result.rejected)
            return (
                combined,
                analyse(combined.audio, rate),
                audition_pair(original, combined.audio),
                operations,
            )

        self.owner.start_job(
            calculate,
            self.owner.repair_applied,
            "Reconstructing checked short intervals from intact context…",
        )

    def repair_reported(self, result):
        self.last_attempt = result
        repaired = set(result.repaired)
        rejected = {item.interval: item.reason for item in result.rejected}
        with QtCore.QSignalBlocker(self.table):
            for row, (interval, _) in enumerate(self.rows):
                text = (
                    "Repaired"
                    if interval in repaired
                    else ("Skipped: " + rejected[interval] if interval in rejected else "")
                )
                self.table.item(row, 7).setText(text)
                self.table.item(row, 7).setToolTip(text)
                self.table.item(row, 7).setData(QtCore.Qt.ItemDataRole.UserRole, text)
        reasons = "; ".join(sorted({item.reason for item in result.rejected}))
        self.detailed_repair_summary.setText(
            f"Reconstructed {len(result.repaired)} intervals / {result.changed_samples} samples. "
            f"Skipped {len(result.rejected)}. {reasons}"
        )
        self.repair_summary.setText(
            f"{len(result.repaired)} peaks repaired."
            + (
                f" {len(result.rejected)} kept unchanged."
                if result.rejected
                else " Listen and compare with the original."
            )
        )
        if not result.repaired:
            self.result_heading.setText("No repair applied")
            self.summary.setText(
                "These peaks could not be reconstructed with the current method. Details are available under Advanced."
            )
            if self.owner.repair_result is not None:
                self.repair_summary.setText(
                    self.repair_summary.text() + " Previous repair remains active."
                )
        else:
            self.result_heading.setText("Repair preview ready")
            self.summary.setText(
                "Listen to the repair and switch to the original below. Undo restores the original input."
            )
        self.update_table_view()
