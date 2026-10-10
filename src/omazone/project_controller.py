"""Desktop project actions. Recipes are separate from transient rendered audio."""

import copy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .comparison import Side
from .engine import MatchFilter, MatchSettings, analyse, audition_pair
from .project import (
    MatchCalibration,
    NamedRegion,
    Project,
    hydrate_project,
    load_project,
    save_project,
)
from .waveform import SampleRegion
from .workflow_steps import clamp, is_step_key, legacy_step_key


class ProjectController:
    def setup_project_actions(self):
        menu = self.menuBar().addMenu("Project")
        self.project_actions = []

        def action(title, callback, shortcut=None):
            item = QtGui.QAction(title, self)
            item.triggered.connect(callback)
            if shortcut:
                item.setShortcut(shortcut)
            menu.addAction(item)
            self.project_actions.append(item)
            return item

        action("New project", self.new_project, "Ctrl+N")
        action("Open project…", self.choose_project, "Ctrl+O")
        action("Save project", self.save_current_project, "Ctrl+S")
        action("Save project as…", lambda: self.save_current_project(save_as=True), "Ctrl+Shift+S")
        menu.addSeparator()
        action("Name current selection…", self.name_selection)
        self.named_regions_menu = menu.addMenu("Named regions")
        self.render_saved_action = action(
            "Render saved recipe (keep learned curves)", self.render_saved_recipe
        )
        action("Relink source recording…", lambda: self.relink_recording("source"))
        action("Relink reference recording…", lambda: self.relink_recording("reference"))
        stages = menu.addMenu("Stage bypass")
        self.stage_actions = {}
        for key, title in (
            ("repair", "Skip repair"),
            ("match", "Skip matching"),
            ("eq", "Skip manual EQ"),
            ("dynamics", "Skip compressor"),
            ("output", "Skip output gain"),
        ):
            item = QtGui.QAction(title, self)
            item.setCheckable(True)
            item.setChecked(self.project.stages[key].bypassed)
            item.toggled.connect(lambda checked, stage=key: self.set_stage_bypass(stage, checked))
            stages.addAction(item)
            self.stage_actions[key] = item

    def sync_project(self):
        if self.restoring_project:
            return
        self.project.targets = dict(self.section_workbench.targets)
        self.project.import_sections(self.section_workbench.sections)
        old = self.project.matching
        self.project.matching = MatchSettings(
            self.retained_number(self.amount, old.amount, 100),
            self.retained_number(self.smoothing, old.smoothing_octaves),
            self.retained_number(self.boost, old.max_boost_db),
            self.retained_number(self.cut, old.max_cut_db),
            old.taps,
        )
        self.project.transition_ms = self.retained_number(
            self.section_workbench.transition_ms, self.project.transition_ms
        )
        region = self.waveform.selection
        loop = self.transport.region
        self.project.view.update(
            {
                "selection": [region.start, region.end] if region else None,
                "loop_region": [loop.start, loop.end] if loop else None,
                "loop_enabled": self.transport.loop,
                "position": self.position,
                "audition_mode": self.audition_mode,
                "active_step": self.views.currentStep(),
            }
        )
        self.project.view.pop("active_tool", None)
        if self.waveform.channel_plots:
            self.project.view["zoom"] = list(self.waveform.channel_plots[0].viewRange()[0])
        reference_view = self.section_workbench.reference_waveform
        if reference_view.index is not None:
            selected = reference_view.selection
            self.project.view["reference_selection"] = (
                [selected.start, selected.end] if selected else None
            )
            self.project.view["reference_zoom"] = list(
                reference_view.channel_plots[0].viewRange()[0]
            )
        if hasattr(self, "workspace"):
            self.project.view.update(self.workspace.preferences())

    def project_changed(self, processing=True):
        if self.restoring_project:
            return
        self.sync_project()
        self.project.dirty = True
        self.project.revision += 1
        if processing:
            self.project.needs_render = True
        self.update_project_title()

    def update_project_title(self):
        name = self.project_path.name if self.project_path else "Unsaved project"
        self.setWindowTitle(f"Omazone | {name}{' *' if self.project.dirty else ''}")

    @staticmethod
    def retained_number(control, original, scale=1):
        # Preserve unexposed precision when merely opening/saving a recipe.
        if abs(control.value() - original * scale) <= 0.5 * 10 ** (-control.decimals()) + 1e-12:
            return original
        return control.value() / scale

    def update_project_actions(self):
        if not hasattr(self, "project_actions"):
            return
        busy = self.worker is not None
        self.named_regions_menu.setEnabled(not busy)
        for item in self.project_actions:
            item.setEnabled(not busy)
        workflow = self.workflow_status()
        self.render_saved_action.setEnabled(not busy and workflow.render_allowed)
        for key, item in self.stage_actions.items():
            with QtCore.QSignalBlocker(item):
                item.setChecked(self.project.stages[key].bypassed)
            item.setEnabled(
                not busy
                and (
                    key in ("repair", "match", "eq", "dynamics", "output")
                    or not self.project.stages[key].bypassed
                )
            )

    def refresh_named_regions(self):
        self.named_regions_menu.clear()
        if hasattr(self, "region_list"):
            self.region_list.clear()
        for region in self.project.regions:
            if hasattr(self, "region_list"):
                self.region_list.addItem(region.name)
            item = self.named_regions_menu.addAction(region.name)
            item.triggered.connect(
                lambda checked=False, selected=region: self.waveform.set_selection(selected.bounds)
            )
        if hasattr(self, "workspace"):
            self.workspace.refresh()
            self.section_workbench.refresh_overlays()
        if hasattr(self, "manual_eq_view"):
            self.manual_eq_view.refresh_regions()

    def name_selection(self):
        region = self.waveform.selection
        if region is None or self.project.source is None:
            self.error("Load a recording and select a passage first.")
            return
        name = self.region_name.text().strip() if hasattr(self, "region_name") else ""
        ok = bool(name)
        if not ok:
            name, ok = QtWidgets.QInputDialog.getText(self, "Name selected passage", "Name")
        if ok and name.strip():
            self.project.regions.append(NamedRegion(uuid4().hex, name.strip(), region))
            self.project_changed(processing=False)
            self.refresh_named_regions()

    def begin_source_project(self, data):
        previous = self.project
        self.project = Project(
            source=data[5] if len(data) > 5 else None,
            reference=previous.reference,
            reference_target=previous.reference_target,
            targets=dict(self.section_workbench.targets),
        )
        self.project_path = None
        self.asset_messages = []
        self.repair_unavailable = False

    def new_project(self):
        self.guard_unsaved(self.reset_project)

    def reset_project(self):
        self.stop()
        self.restoring_project = True
        try:
            self.project = Project()
            self.renderer = None
            self.renderer_key = None
            self.project_path = None
            for control, value in (
                (self.amount, 50),
                (self.smoothing, 0.33),
                (self.boost, 6),
                (self.cut, 6),
                (self.section_workbench.transition_ms, 75),
            ):
                with QtCore.QSignalBlocker(control):
                    control.setValue(value)
            self.source = self.reference = None
            self.repair_result = self.repaired_source = self.repair_preview = None
            self.apply_comparison("mastering", Side.BEFORE)
            self.invalidate(record=False)
            self.waveform.clear_audio()
            self.section_workbench.reference_waveform.clear_audio()
            self.section_workbench.targets = {}
            self.section_workbench.sections = []
            self.section_workbench.refresh_targets()
            self.section_workbench.refresh()
            self.clipping_inspector.clear()
            self.clipping_inspector.reset_repair_status()
            self.reset_playback_mode()
            self.position = 0
            self.asset_messages = []
            self.repair_unavailable = False
        finally:
            self.restoring_project = False
        self.refresh_named_regions()
        self.manual_eq_view.restore()
        self.compressor_view.restore()
        self.output_view.restore()
        self.update_project_title()
        self.refresh_file_labels()
        self.plot_spectra()
        self.update_buttons()
        self.status.setText("New project. Load a recording; saved project files are unchanged.")

    def guard_unsaved(self, continuation):
        if not self.project.dirty:
            continuation()
            return True
        choice = QtWidgets.QMessageBox.question(
            self,
            "Unsaved project",
            "Save changes before continuing?",
            QtWidgets.QMessageBox.StandardButton.Save
            | QtWidgets.QMessageBox.StandardButton.Discard
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Save,
        )
        if choice == QtWidgets.QMessageBox.StandardButton.Discard:
            continuation()
            return True
        if choice == QtWidgets.QMessageBox.StandardButton.Save:
            return self.save_current_project(completed=continuation)
        return False

    def save_current_project(self, checked=False, save_as=False, completed=None):
        if self.source is not None and self.project.source is None:
            self.error(
                "This recording has no file reference. Load it from a file before saving a project."
            )
            return False
        if self.section_workbench.draft_dirty and not self.section_workbench.update_section():
            return False
        self.stop()
        self.sync_project()
        path = self.project_path
        if save_as or path is None:
            chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Save Omazone project",
                "session.omazone.json",
                "Omazone project (*.omazone.json)",
            )
            if not chosen:
                return False
            path = Path(chosen)
            if not str(path).endswith(".omazone.json"):
                path = Path(str(path) + ".omazone.json")
        saved_project = self.project
        saved_revision = saved_project.revision
        snapshot = copy.deepcopy(saved_project)
        self.start_job(
            lambda: save_project(path, snapshot),
            lambda _: self.project_saved(path, saved_project, saved_revision, completed),
            "Saving project recipe…",
        )
        return True

    def project_saved(self, path, saved_project=None, saved_revision=None, completed=None):
        saved_project = saved_project or self.project
        saved_revision = saved_project.revision if saved_revision is None else saved_revision
        if self.project is not saved_project or self.project.revision != saved_revision:
            self.status.setText(f"Project snapshot saved: {path}. Newer changes remain unsaved.")
            return
        self.project_path = Path(path)
        self.project.mark_saved()
        self.update_project_title()
        self.status.setText(f"Project saved: {path}")
        if completed is not None:
            completed()

    def choose_project(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Omazone project", "", "Omazone project (*.omazone.json);;JSON (*.json)"
        )
        if path:
            self.open_project_path(path)

    def open_project_path(self, path):
        self.guard_unsaved(lambda: self.load_project_path(path))

    def load_project_path(self, path):
        self.start_job(
            lambda: hydrate_project(load_project(path)),
            lambda loaded: self.install_project(loaded, path),
            "Opening project and checking recordings…",
        )

    def install_project(self, loaded, path):
        project = loaded.project
        self.stop()
        self.restoring_project = True
        try:
            self.project = project
            self.renderer = None
            self.renderer_key = None
            self.project_path = Path(path) if path else None
            self.source = self.reference = None
            self.repair_result = self.repaired_source = self.repair_preview = None
            self.apply_comparison("mastering", Side.BEFORE)
            self.invalidate(record=False)
            self.reset_playback_mode()
            self.waveform.clear_audio()
            self.section_workbench.reference_waveform.clear_audio()
            self.section_workbench.targets = dict(project.targets)
            self.section_workbench.sections = []
            self.clipping_inspector.clear()
            self.clipping_inspector.reset_repair_status()
            if loaded.source is not None:
                self.loaded("source", loaded.source)
            elif project.source:
                self.waveform.sample_rate = project.source.sample_rate
                with QtCore.QSignalBlocker(self.seek_slider):
                    self.seek_slider.setRange(0, project.source.frames)
                    self.seek_slider.setSingleStep(project.source.sample_rate)
                    self.seek_slider.setPageStep(project.source.sample_rate * 10)
                for control in (self.waveform.start_time, self.waveform.end_time):
                    with QtCore.QSignalBlocker(control):
                        control.setRange(0, project.source.frames / project.source.sample_rate)
            if loaded.reference is not None:
                self.loaded("reference", loaded.reference)
                if project.view.get("reference_selection"):
                    self.section_workbench.reference_waveform.set_selection(
                        SampleRegion(*project.view["reference_selection"])
                    )
                if project.view.get("reference_zoom"):
                    self.section_workbench.reference_waveform.channel_plots[0].setXRange(
                        *project.view["reference_zoom"], padding=0
                    )
            self.section_workbench.targets = dict(project.targets)
            self.section_workbench.sections = list(project.sections)
            self.section_workbench.refresh_targets()
            for control, value in (
                (self.amount, project.matching.amount * 100),
                (self.smoothing, project.matching.smoothing_octaves),
                (self.boost, project.matching.max_boost_db),
                (self.cut, project.matching.max_cut_db),
                (self.section_workbench.transition_ms, project.transition_ms),
            ):
                with QtCore.QSignalBlocker(control):
                    control.setRange(min(control.minimum(), value), max(control.maximum(), value))
                    control.setValue(value)
            self.section_workbench.refresh()
            if loaded.repair is not None:
                repair = loaded.repair
                self.repair_applied(
                    (
                        repair,
                        analyse(repair.audio, self.source[1]),
                        audition_pair(self.source[0], repair.audio),
                    )
                )
            view = project.view
            if view.get("selection"):
                self.waveform.set_selection(SampleRegion(*view["selection"]))
            if self.source is not None and view.get("zoom"):
                self.waveform.channel_plots[0].setXRange(*view["zoom"], padding=0)
            self.position = view.get("position", 0)
            loop = view.get("loop_region")
            if project.source is not None and loop:
                self.transport.region = SampleRegion(*loop)
                self.transport.loop = bool(view.get("loop_enabled", False))
                with QtCore.QSignalBlocker(self.loop_selection):
                    self.loop_selection.setChecked(self.transport.loop)
            # Older projects saved a tab position, which cannot survive navigation
            # being reordered, so fall back to it only when no step name is stored.
            saved = view.get("active_step")
            self.views.setStep(
                clamp(
                    legacy_step_key(saved)
                    if is_step_key(saved)
                    else legacy_step_key(view.get("active_tool"))
                )
            )
            if hasattr(self, "workspace"):
                self.workspace.restore_preferences(view)
        finally:
            self.restoring_project = False
        self.asset_messages = loaded.messages
        self.repair_unavailable = bool(
            project.repairs and loaded.source is not None and loaded.repair is None
        )
        self.project.mark_saved()
        self.project.needs_render = True
        self.refresh_named_regions()
        self.update_project_title()
        self.manual_eq_view.restore()
        self.compressor_view.restore()
        self.output_view.restore()
        self.plot_spectra()
        self.refresh_file_labels()
        self.update_transport()
        self.update_buttons()
        self.status.setText(
            " | ".join(loaded.messages)
            or "Project restored. Render saved recipe to keep its learned matching; Analyse explicitly to relearn."
        )

    def relink_recording(self, kind):
        if getattr(self.project, kind) is None:
            self.error("There is no saved recording reference to relink.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Locate original recording", "", "Audio (*.wav *.flac *.aiff *.aif)"
        )
        if not path:
            return
        original = getattr(self.project, kind)

        def relink():
            self.project.relink(kind, path)
            try:
                return hydrate_project(self.project)
            except Exception:
                setattr(self.project, kind, original)
                raise

        def completed(loaded):
            self.install_project(loaded, self.project_path)
            self.project.dirty = True
            self.update_project_title()

        self.start_job(relink, completed, "Checking relinked recording…")

    def set_stage_bypass(self, stage, checked):
        self.project.stages[stage].bypassed = checked
        if stage == "eq":
            self.invalidate_eq()
            self.manual_eq_view.restore()
        elif stage == "dynamics":
            self.invalidate_dynamics()
            self.compressor_view.restore()
        elif stage == "output":
            self.invalidate_output()
            self.output_view.restore()
        else:
            self.invalidate()
        self.plot_spectra()
        self.status.setText(
            f"{stage.capitalize()} {'skipped' if checked else 'enabled'}. Choices retained; output needs rendering."
        )

    def render_saved_recipe(self):
        if not self.ensure_processing_ready():
            return
        self.sync_project()
        workflow = self.workflow_status()
        if not workflow.render_allowed:
            self.error(workflow.render_reason)
            return
        snapshot = copy.deepcopy(self.project)
        renderer = self.get_renderer()

        def calculate():
            result = renderer.render(snapshot)
            calibration = snapshot.calibration
            if calibration and calibration.whole and snapshot.match_mode == "whole":
                spec = calibration.whole
            elif calibration and calibration.sections and snapshot.match_mode == "sections":
                spec = calibration.sections[0].filter
            else:
                spec = MatchFilter(
                    np.ones(1), np.asarray([0, renderer.rate / 2]), np.zeros(2), renderer.rate
                )
            return (
                result.output,
                analyse(result.output, renderer.rate),
                spec,
                audition_pair(result.repaired, result.matched),
                result,
                audition_pair(result.matched, result.equalized),
                audition_pair(result.equalized, result.pre_output),
                audition_pair(result.pre_output, result.output),
            )

        def completed(payload):
            self.processed(payload, learn=False)
            self.project.needs_render = False
            if self.views.currentWidget() is self.manual_eq_view:
                self.manual_eq_view.draw_response()
                self.manual_eq_view.summary.setText(
                    "Rendered. Use the listening button to compare before/after this EQ step; "
                    "export contains the full chain."
                )
            self.status.setText(
                "Rendered saved recipe without relearning."
                + (
                    " Matching analysis was retained from an earlier input."
                    if self.workflow_status().matching_analysis.value == "retained"
                    else ""
                )
            )

        self.start_job(calculate, completed, "Rendering saved choices from the original…")

    def ensure_processing_ready(self):
        if self.source is None:
            self.error("Load or relink the original recording first.")
            return False
        if self.repair_unavailable and not self.project.stages["repair"].bypassed:
            self.error(
                "Saved repair could not be replayed. Its choices are retained; reassess it, reset it, or bypass repair."
            )
            return False
        return True

    def add_repair_recipe(self, operation):
        operations = []
        channels = {item.channel for item in operation.accepted}
        for old in self.project.repairs:
            kept = []
            for item in old.accepted:
                overlap = item.channel in channels and max(
                    item.start, operation.region.start
                ) < min(item.end, operation.region.end)
                if (
                    overlap
                    and not operation.region.start <= item.start < item.end <= operation.region.end
                ):
                    raise ValueError(
                        "Selection cuts an existing repair. Expand it to include that whole interval."
                    )
                if not overlap:
                    kept.append(item)
            if kept:
                operations.append(replace(old, accepted=tuple(kept)))
        operations.append(operation)
        return operations

    def record_calibration(self, whole=None, sections=()):
        self.sync_project()
        self.project.calibration = MatchCalibration(
            self.project.config_key(), self.project.input_key(), whole, tuple(sections)
        )
        self.project.needs_render = False
        self.project.dirty = True
        self.project.revision += 1
        self.update_project_title()
