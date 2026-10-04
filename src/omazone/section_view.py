"""Reference-target capture and section-assignment controls for the workbench."""

from dataclasses import replace
from uuid import uuid4

from PySide6 import QtCore, QtGui, QtWidgets

from .engine import MatchSettings, analyse, audition_pair
from .sections import (
    SectionAssignment,
    capture_target,
    load_targets,
    render_sections,
    save_targets,
    section_curve,
    transition_plan,
    validate_sections,
)
from .waveform import PeakIndex, SampleRegion
from .waveform_view import WaveformView

SECTION_COLORS = ("#73a8ff", "#eabb6b", "#c99bff", "#63dfc0", "#f48b98")


class SectionWorkbench(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.targets = {}
        self.sections = []
        self.form_loading = False
        self.draft_dirty = False
        self.reference_page = QtWidgets.QWidget()
        reference_layout = QtWidgets.QVBoxLayout(self.reference_page)
        reference_layout.setContentsMargins(0, 0, 0, 0)
        reference_layout.addWidget(
            QtWidgets.QLabel(
                "Select a reference passage, name it, and capture a target. Mix selection is independent."
            )
        )
        row = QtWidgets.QHBoxLayout()
        self.target_name = QtWidgets.QLineEdit()
        self.target_name.setPlaceholderText("Target name, e.g. Metal or Clean")
        row.addWidget(self.target_name, 1)
        self.capture_button = self.button(row, "Capture target", self.capture)
        self.button(row, "Save targets", self.save_library)
        self.button(row, "Load targets", self.load_library)
        self.button(row, "Remove target", self.remove_target)
        reference_layout.addLayout(row)
        self.target_list = QtWidgets.QListWidget()
        self.target_list.setMaximumHeight(100)
        reference_layout.addWidget(self.target_list)
        self.reference_waveform = WaveformView()
        self.reference_waveform.buttons[3].hide()  # Reference audition is not the mix transport.
        self.reference_waveform.help_text.setText(
            "Reference only | Wheel: zoom | Drag: pan | Shift+drag: select | Drag green edges: adjust"
        )
        reference_layout.addWidget(self.reference_waveform, 1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        instructions = QtWidgets.QLabel(
            "Capture targets in Reference targets. Select a mix passage in Waveform / selection, "
            "then Use mix selection and Add section. Select a row to inspect or audition it."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("Name", "Start (s)", "End (s)", "Target", "Amount", "Smooth (oct)", "Boost / cut (dB)")
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.select_row)
        layout.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("Section name")
        row.addWidget(self.name, 1)
        self.target_choice = QtWidgets.QComboBox()
        row.addWidget(self.target_choice, 1)
        self.start = self.spin(row, "Start", 0, 1e8, 0, " s", 9)
        self.end = self.spin(row, "End", 0, 1e8, 0, " s", 9)
        layout.addLayout(row)
        settings_row = QtWidgets.QHBoxLayout()
        self.amount = self.spin(settings_row, "Amount", 0, 100, 50, "%", 0)
        self.smoothing = self.spin(settings_row, "Smooth", 0.02, 2, 0.33, " oct", 2)
        self.boost = self.spin(settings_row, "Max boost", 0, 18, 6, " dB", 1)
        self.cut = self.spin(settings_row, "Max cut", 0, 18, 6, " dB", 1)
        layout.addLayout(settings_row)
        row = QtWidgets.QHBoxLayout()
        self.button(row, "Use mix selection", self.use_mix_selection)
        self.button(row, "Add section", self.add_section)
        self.button(row, "Apply to selected", self.update_section)
        self.button(row, "Remove section", self.remove_section)
        self.button(row, "Inspect EQ", self.inspect)
        self.button(row, "Audition section", self.audition)
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.transition_ms = self.spin(row, "Transition", 0, 5000, 75, " ms", 1)
        self.transition_ms.valueChanged.connect(self.transition_changed)
        self.render_button = self.button(row, "Render sections", self.render_all)
        layout.addLayout(row)
        self.summary = QtWidgets.QLabel(
            "Unassigned passages stay dry except in yellow transition windows."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        for control in (self.start, self.end, self.amount, self.smoothing, self.boost, self.cut):
            control.valueChanged.connect(self.mark_draft)
        self.name.textEdited.connect(self.mark_draft)
        self.target_choice.currentIndexChanged.connect(self.mark_draft)

    def button(self, row, text, callback):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def spin(self, row, label, low, high, value, suffix, decimals):
        row.addWidget(QtWidgets.QLabel(label))
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(low, high)
        control.setDecimals(decimals)
        control.setValue(value)
        control.setSuffix(suffix)
        control.setSingleStep(1 if decimals == 0 else 0.1)
        row.addWidget(control)
        return control

    def set_reference(self, data):
        index = data[4] if len(data) > 4 else PeakIndex(data[0])
        self.reference_waveform.set_audio(index, data[1])
        for playhead in self.reference_waveform.playheads:
            playhead.hide()

    def reset_mix(self):
        self.sections = []
        for control in (self.start, self.end):
            control.setValue(0)
            control.setSingleStep(1 / self.owner.source[1])
        self.refresh()

    def refresh_targets(self):
        selected = self.target_choice.currentData()
        blocker = QtCore.QSignalBlocker(self.target_choice)
        self.target_choice.clear()
        self.target_list.clear()
        for profile in self.targets.values():
            self.target_choice.addItem(profile.name, profile.id)
            duration = (profile.region.end - profile.region.start) / profile.sample_rate
            item = QtWidgets.QListWidgetItem(
                f"{profile.name} | {profile.source_name} | {profile.region.start / profile.sample_rate:.3f}-"
                f"{profile.region.end / profile.sample_rate:.3f} s | {duration:.3f} s | {profile.sample_rate} Hz"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, profile.id)
            self.target_list.addItem(item)
        index = self.target_choice.findData(selected)
        if index >= 0:
            self.target_choice.setCurrentIndex(index)
        del blocker

    def capture(self):
        reference = self.owner.reference
        region = self.reference_waveform.selection
        if reference is None or region is None:
            self.owner.error("Load a reference and select a passage first.")
            return
        name = self.target_name.text()
        target_id = uuid4().hex
        self.owner.start_job(
            lambda: capture_target(
                reference[0], reference[1], region, name, target_id, reference[3]
            ),
            self.target_captured,
            "Analysing reference passage…",
        )

    def target_captured(self, profile):
        self.targets[profile.id] = profile
        self.refresh_targets()
        if self.selected_section() is None:
            self.target_choice.setCurrentIndex(self.target_choice.findData(profile.id))
        self.owner.status.setText(f"Captured target: {profile.name}")

    def save_library(self):
        if not self.targets:
            self.owner.error("Capture a target first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save target library", "targets.json", "JSON (*.json)"
        )
        if path:
            if not path.lower().endswith(".json"):
                path += ".json"
            profiles = tuple(self.targets.values())
            self.owner.start_job(
                lambda: save_targets(path, profiles),
                lambda _: self.owner.status.setText("Target library saved."),
                "Saving targets…",
            )

    def load_library(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load target library", "", "JSON (*.json)"
        )
        if path:
            self.owner.start_job(
                lambda: load_targets(path), self.targets_loaded, "Loading targets…"
            )

    def targets_loaded(self, profiles):
        for profile in profiles:
            if profile.id in self.targets:
                profile = replace(profile, id=uuid4().hex, name=profile.name + " (import)")
            self.targets[profile.id] = profile
        self.refresh_targets()
        self.owner.status.setText(
            f"Imported {len(profiles)} targets. Existing assignments preserved."
        )

    def remove_target(self):
        item = self.target_list.currentItem()
        if item is None:
            return
        target_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if any(section.target_id == target_id for section in self.sections):
            self.owner.error("Remove or reassign sections using this target first.")
            return
        del self.targets[target_id]
        self.refresh_targets()

    def form_section(self, section_id):
        if self.owner.source is None:
            raise ValueError("Load a mix first.")
        rate = self.owner.source[1]
        return SectionAssignment(
            section_id,
            self.name.text().strip(),
            SampleRegion(round(self.start.value() * rate), round(self.end.value() * rate)),
            self.target_choice.currentData(),
            MatchSettings(
                self.amount.value() / 100,
                self.smoothing.value(),
                self.boost.value(),
                self.cut.value(),
            ),
        )

    def use_mix_selection(self):
        region = self.owner.waveform.selection
        if region is None or self.owner.source is None:
            self.owner.error("Select a passage in Waveform / selection first.")
            return
        self.table.clearSelection()
        self.draft_dirty = False
        self.start.setValue(region.start / self.owner.source[1])
        self.end.setValue(region.end / self.owner.source[1])
        self.name.setText(f"Section {len(self.sections) + 1}")

    def commit_sections(self, sections, selected_id=None):
        try:
            ordered = validate_sections(
                sections, self.targets, len(self.owner.source[0]), self.owner.source[1]
            )
        except ValueError as error:
            self.owner.error(str(error))
            return False
        self.sections = ordered
        self.owner.invalidate()
        self.owner.plot_spectra()
        self.refresh(selected_id)
        self.owner.status.setText("Section plan changed. Render sections to update A/B and export.")
        return True

    def add_section(self):
        try:
            section = self.form_section(uuid4().hex)
            self.commit_sections([*self.sections, section], section.id)
        except ValueError as error:
            self.owner.error(str(error))

    def selected_section(self):
        row = self.table.currentRow()
        return (
            self.sections[row]
            if self.table.selectedItems() and 0 <= row < len(self.sections)
            else None
        )

    def update_section(self):
        section = self.selected_section()
        if section is None:
            return False
        try:
            replacement = self.form_section(section.id)
            return self.commit_sections(
                [replacement if item.id == section.id else item for item in self.sections],
                section.id,
            )
        except ValueError as error:
            self.owner.error(str(error))
            return False

    def remove_section(self):
        section = self.selected_section()
        if section is not None:
            self.commit_sections([item for item in self.sections if item.id != section.id])

    def refresh(self, selected_id=None):
        self.draft_dirty = False
        with QtCore.QSignalBlocker(self.table):
            self.table.setRowCount(len(self.sections))
            for row, section in enumerate(self.sections):
                rate = self.owner.source[1]
                values = (
                    section.name,
                    f"{section.region.start / rate:.6f}",
                    f"{section.region.end / rate:.6f}",
                    self.targets[section.target_id].name,
                    f"{section.settings.amount * 100:g}%",
                    f"{section.settings.smoothing_octaves:g}",
                    f"{section.settings.max_boost_db:g} / {section.settings.max_cut_db:g}",
                )
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setForeground(QtGui.QColor(SECTION_COLORS[row % len(SECTION_COLORS)]))
                    self.table.setItem(row, column, item)
                if section.id == selected_id:
                    self.table.selectRow(row)
        self.refresh_overlays()
        self.owner.update_buttons()
        if selected_id is not None:
            self.select_row()

    def select_row(self):
        section = self.selected_section()
        if section is None:
            return
        self.form_loading = True
        self.name.setText(section.name)
        self.start.setValue(section.region.start / self.owner.source[1])
        self.end.setValue(section.region.end / self.owner.source[1])
        self.target_choice.setCurrentIndex(self.target_choice.findData(section.target_id))
        self.amount.setValue(section.settings.amount * 100)
        self.smoothing.setValue(section.settings.smoothing_octaves)
        self.boost.setValue(section.settings.max_boost_db)
        self.cut.setValue(section.settings.max_cut_db)
        self.form_loading = False
        self.draft_dirty = False
        self.owner.waveform.set_selection(section.region)
        if self.owner.section_result is not None:
            for curve in self.owner.section_result.curves:
                if curve.section.id == section.id:
                    self.owner.show_section_curve(curve, switch_view=False)
                    break

    def refresh_overlays(self):
        if self.owner.source is None:
            return
        transitions = transition_plan(
            self.sections,
            len(self.owner.source[0]),
            self.owner.source[1],
            self.transition_ms.value(),
        )
        self.owner.waveform.set_sections(self.sections, transitions, SECTION_COLORS)
        durations = [1000 * (item.end - item.start) / self.owner.source[1] for item in transitions]
        details = ", ".join(f"{value:.1f}" for value in durations) or "none"
        self.summary.setText(
            f"{len(self.sections)} sections | Effective transition windows (ms): {details}. Yellow windows may extend into dry gaps."
        )

    def transition_changed(self):
        self.owner.invalidate()
        self.refresh_overlays()

    def inspect(self):
        if self.draft_dirty and not self.update_section():
            return
        section = self.selected_section()
        if section is None:
            return
        source = self.owner.source
        targets = dict(self.targets)
        self.owner.start_job(
            lambda: section_curve(source[0], source[1], section, targets),
            self.owner.show_section_curve,
            "Designing section correction…",
        )

    def audition(self):
        section = self.selected_section()
        if section is not None:
            self.owner.waveform.set_selection(section.region)
            self.owner.play_selection()

    def render_all(self):
        if self.draft_dirty and not self.update_section():
            return
        if self.owner.source is None or not self.sections:
            self.owner.error("Load a mix and add at least one section.")
            return
        source = self.owner.source
        sections, targets = tuple(self.sections), dict(self.targets)
        transition_ms = self.transition_ms.value()
        self.owner.invalidate()

        def calculate():
            result = render_sections(source[0], source[1], sections, targets, transition_ms)
            return result, analyse(result.audio, source[1]), audition_pair(source[0], result.audio)

        self.owner.start_job(
            calculate,
            self.owner.sections_rendered,
            "Analysing each section and rendering aligned transitions…",
        )

    def mark_draft(self, *args):
        if not self.form_loading and self.selected_section() is not None:
            self.draft_dirty = True
            self.owner.invalidate()
            self.owner.status.setText("Section edits invalidate the preview. Apply to selected, Inspect EQ, or Render sections to apply them.")
