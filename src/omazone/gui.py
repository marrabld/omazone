"""Qt desktop workbench. DSP and file loading run outside the UI thread."""

import argparse
import contextlib
import copy
import math
import sys
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import soundfile as sf
from PySide6 import QtCore, QtGui, QtWidgets

from .clipping_view import ClippingInspector
from .comparison import ORIGINAL as ORIGINAL_ONLY
from .comparison import SPECS, Side, StagePair, resolve, spec_for
from .compressor_view import CompressorView
from .engine import (
    analyse,
    audition_pair,
    design_match,
    peak_db,
    rms_db,
)
from .export_view import ExportView
from .manual_eq_view import ManualEQView
from .output_view import OutputView
from .pipeline import ChainRenderer
from .playback import PlaybackCursor
from .project import STAGES, AudioReference, MatchCalibration, Project
from .project_controller import ProjectController
from .section_view import SectionWorkbench
from .sections import TargetProfile
from .tool_panel import ToolPanel
from .waveform import PeakIndex, SampleRegion
from .waveform_view import WaveformView
from .workflow_status import (
    AnalysisState,
    RenderState,
    StageStatus,
    derive_workflow_status,
    nav_label,
)
from .workflow_steps import NAVIGATION, next_step, previous_step, step_for
from .workspace import SongWorkspace


class Worker(QtCore.QThread):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.value = None
        self.failure = None

    def run(self):
        try:
            self.value = self.function()
        except Exception as error:  # noqa: BLE001 -- surface worker errors at the UI boundary
            self.failure = str(error)


class SeekSlider(QtWidgets.QSlider):
    """Click anywhere or drag to seek; keyboard navigation remains native Qt."""

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.move_to(event.position().x())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.move_to(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.move_to(event.position().x())
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def move_to(self, x):
        option = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            option,
            QtWidgets.QStyle.SubControl.SC_SliderHandle,
            self,
        )
        span = max(1, self.width() - handle.width())
        position = min(span, max(0, round(x - handle.width() / 2)))
        self.setValue(
            QtWidgets.QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), position, span, option.upsideDown
            )
        )


def load_audio(path):
    audio, rate = sf.read(path, always_2d=True, dtype="float64")
    if audio.shape[1] > 2:
        raise ValueError("This workbench supports mono and stereo files.")
    return (
        audio,
        rate,
        analyse(audio, rate),
        Path(path).name,
        PeakIndex(audio),
        AudioReference.from_path(path),
    )


class FrequencyAxis(pg.AxisItem):
    """Label a log-frequency grid in Hz and kHz without crowding the axis."""

    def tickValues(self, minVal, maxVal, size):
        if not self.logMode:
            return super().tickValues(minVal, maxVal, size)
        low, high = sorted((minVal, maxVal))
        if low == high:
            return []

        ticks = [
            exponent + math.log10(multiplier)
            for exponent in range(math.floor(low), math.ceil(high) + 1)
            for multiplier in range(1, 10)
            if low <= exponent + math.log10(multiplier) <= high
        ]
        preferred = [
            value for value in ticks if round(10 ** (value - math.floor(value))) in (1, 2, 5)
        ]
        metrics = QtGui.QFontMetricsF(self.style["tickFont"] or QtWidgets.QApplication.font())
        labelled = []

        def add_if_clear(value):
            position = (value - low) / (high - low) * size
            width = metrics.horizontalAdvance(self._frequency_label(value))
            if all(
                abs(position - other) >= (width + other_width) / 2 + 10
                for other, other_width, _ in labelled
            ):
                labelled.append((position, width, value))

        for value in preferred:
            add_if_clear(value)
        if len(labelled) < 3:
            for value in ticks:
                add_if_clear(value)

        major = sorted(value for _, _, value in labelled)
        minor = [value for value in ticks if not any(abs(value - tick) < 1e-9 for tick in major)]
        return [(1, major), (None, minor)]

    def tickStrings(self, values, scale, spacing):
        if spacing is None:
            return [""] * len(values)
        return [self._frequency_label(value) for value in values]

    @staticmethod
    def _frequency_label(value):
        frequency = round(10**value, 6)
        if frequency >= 1000:
            return f"{frequency / 1000:g} kHz"
        return f"{frequency:g} Hz"


# Continue has nothing to wait for on a step that explains no processor.
NARROW_WIDTH = 950
READY_STAGE = StageStatus(RenderState.READY, "Ready to move on.", "Continue")


class Window(ProjectController, QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Omazone | spectral matching playground")
        self.resize(1120, 800)
        self.project = Project()
        self.project_path = None
        self.restoring_project = False
        self.asset_messages = []
        self.repair_unavailable = False
        self.source = self.reference = self.output = None
        self.section_result = None
        self.repair_result = None
        self.repaired_source = None
        self.repair_preview = None
        self.mastering_preview = None
        self.eq_preview = None
        self.eq_before = None
        self.dynamics_preview = None
        self.dynamics_before = None
        self.output_preview = None
        self.output_before = None
        self.match_after = None
        self.eq_after = None
        self.dynamics_after = None
        self.output_after = None
        self.chain_result = None
        self.renderer = None
        self.renderer_key = None
        self.audition_mode = "mastering"
        self.worker = None
        self.stream = None
        self.preview = None
        self.transport = PlaybackCursor()
        self.playing = False
        self.listen_processed = False
        self.resume_after_scrub = False
        self.resume_after_selection_edit = False
        self._workflow_status = None
        self._identity_playback = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        heading = QtWidgets.QLabel("OMAZONE   /   spectral laboratory")
        heading.setStyleSheet("font-size: 18px; font-weight: bold; color: #63dfc0;")
        layout.addWidget(heading)
        self.files = QtWidgets.QLabel("Load a mix and a reference to begin.")
        self.files.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.files)

        row = QtWidgets.QHBoxLayout()
        self.load_mix = self.button(row, "Load mix", lambda: self.load("source"))
        self.load_ref = self.button(row, "Load reference", lambda: self.load("reference"))
        self.export_button = self.button(row, "Export WAV", self.export)
        for button in (self.load_mix, self.load_ref, self.export_button):
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Maximum, QtWidgets.QSizePolicy.Policy.Fixed
            )
        row.addStretch(1)
        layout.addLayout(row)

        pg.setConfigOptions(antialias=True, background="#141a24", foreground="#b8c4d6")
        self.views = ToolPanel()
        spectra = QtWidgets.QWidget()
        spectra_layout = QtWidgets.QVBoxLayout(spectra)
        spectra_layout.setContentsMargins(0, 0, 0, 0)
        self.spectrum_plot = self.plot("Relative spectral power", "dB", spectra_layout)
        self.spectrum_plot.addLegend()
        self.eq_plot = self.plot("Filter gain", "dB", spectra_layout)
        self.eq_plot.addLegend()
        self.eq_plot.setYRange(-8, 8)
        self.eq_plot.setXLink(self.spectrum_plot)
        spectra_layout.setStretch(0, 3)
        spectra_layout.setStretch(1, 2)
        self.match_page = QtWidgets.QWidget()
        match_layout = QtWidgets.QVBoxLayout(self.match_page)
        match_layout.setContentsMargins(0, 0, 0, 0)
        self.waveform = WaveformView()
        self.waveform.seek_requested.connect(self.seek)
        self.waveform.selection_changed.connect(self.selection_changed)
        self.selection_timer = QtCore.QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.setInterval(150)
        self.selection_timer.timeout.connect(self.finish_selection_edit)
        self.region_page = QtWidgets.QWidget()
        region_layout = QtWidgets.QVBoxLayout(self.region_page)
        region_layout.setContentsMargins(0, 0, 0, 0)
        region_layout.addWidget(
            QtWidgets.QLabel(
                "Select in the shared waveform or overview. Name a passage to retain it independently of effects."
            )
        )
        self.region_name = QtWidgets.QLineEdit()
        self.region_name.setPlaceholderText("Passage name, e.g. Acoustic verse")
        region_layout.addWidget(self.region_name)
        self.region_list = QtWidgets.QListWidget()
        self.region_list.setMaximumHeight(180)
        self.region_list.itemClicked.connect(
            lambda item: self.waveform.set_selection(
                self.project.regions[self.region_list.row(item)].bounds
            )
        )
        region_layout.addWidget(self.region_list)
        self.name_region_button = QtWidgets.QPushButton("Name current selection…")
        self.name_region_button.clicked.connect(self.name_selection)
        region_layout.addWidget(self.name_region_button)
        self.region_advanced_toggle = QtWidgets.QToolButton()
        self.region_advanced_toggle.setText("Precise selection bounds")
        self.region_advanced_toggle.setCheckable(True)
        self.region_advanced_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.region_advanced_toggle.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        region_layout.addWidget(self.region_advanced_toggle)
        self.region_advanced_panel = QtWidgets.QWidget()
        region_advanced = QtWidgets.QVBoxLayout(self.region_advanced_panel)
        self.waveform.selection_controls.layout().setDirection(
            QtWidgets.QBoxLayout.Direction.TopToBottom
        )
        region_advanced.addWidget(self.waveform.selection_controls)
        region_advanced.addWidget(self.waveform.selection_label)
        region_layout.addWidget(self.region_advanced_panel)
        self.region_advanced_panel.hide()
        self.region_advanced_toggle.toggled.connect(self.region_advanced_panel.setVisible)
        region_layout.addStretch(1)
        self.section_workbench = SectionWorkbench(self)
        self.clipping_inspector = ClippingInspector(self)
        self.manual_eq_view = ManualEQView(self)
        self.compressor_view = CompressorView(self)
        self.output_view = OutputView(self)
        self.export_view = ExportView(self)
        # Register every page once, in the order a learner moves through them.
        # A step owns the navigation entry, so the order here is the workflow.
        for page, key in (
            (self.region_page, "listen"),
            (self.clipping_inspector, "repair"),
            (self.match_page, "match"),
            (self.section_workbench.reference_page, "reference"),
            (self.section_workbench, "sections"),
            (self.manual_eq_view, "eq"),
            (self.compressor_view, "dynamics"),
            (self.output_view, "output"),
            (self.export_view, "export"),
        ):
            self.views.addTab(page, step_for(key))
        reference_waveform = self.section_workbench.reference_waveform
        reference_layout = self.section_workbench.reference_page.layout()
        reference_layout.removeWidget(reference_waveform)
        reference_waveform.selection_controls.layout().setDirection(
            QtWidgets.QBoxLayout.Direction.TopToBottom
        )
        self.section_workbench.reference_advanced_layout.addWidget(
            reference_waveform.selection_controls
        )
        self.section_workbench.reference_advanced_layout.addWidget(
            reference_waveform.selection_label
        )
        self.workspace = SongWorkspace(
            self,
            self.waveform,
            spectra,
            reference_waveform,
            self.manual_eq_view.canvas,
            self.compressor_view.canvas,
            self.output_view.canvas,
        )
        self.workspace_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.workspace_split.addWidget(self.workspace)
        self.tool_scroll = self.views.scroll_area
        self.views.setMinimumHeight(175)
        self.workspace_split.addWidget(self.views)
        self.workspace_split.setChildrenCollapsible(False)
        self.workspace_split.setSizes([360, 260])
        layout.addWidget(self.workspace_split, 1)
        self.views.layout().removeWidget(self.views.navigation)
        # Alt+arrow moves between steps. Plain arrows stay with the seek slider.
        self.back_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence("Alt+Left"),
            self,
            context=QtCore.Qt.ShortcutContext.WindowShortcut,
        )
        self.back_shortcut.activated.connect(self.go_back)
        self.continue_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence("Alt+Right"),
            self,
            context=QtCore.Qt.ShortcutContext.WindowShortcut,
        )
        self.continue_shortcut.activated.connect(self.go_continue)
        self.back_button = QtWidgets.QPushButton("Back")
        self.back_button.clicked.connect(self.go_back)
        self.skip_button = QtWidgets.QPushButton("Skip this step")
        self.skip_button.clicked.connect(self.toggle_skip_step)
        self.continue_button = QtWidgets.QPushButton("Continue")
        self.continue_button.clicked.connect(self.go_continue)
        for control in (self.back_button, self.continue_button):
            self.views.control_layout.addWidget(control)
        self.views.control_layout.insertWidget(1, self.skip_button)
        layout.insertWidget(layout.indexOf(self.workspace_split), self.views.navigation)

        self.mastering_controls = QtWidgets.QWidget()
        mastering_layout = QtWidgets.QVBoxLayout(self.mastering_controls)
        mastering_layout.setContentsMargins(0, 0, 0, 0)
        self.mastering_controls.setToolTip(
            "Whole-song matching settings. Mix sections have independent settings."
        )
        controls = QtWidgets.QVBoxLayout()
        self.amount = self.control(controls, "Match amount", 0, 100, 50, "%", 0)
        mastering_layout.addLayout(controls)
        self.match_advanced_toggle = QtWidgets.QToolButton()
        self.match_advanced_toggle.setText("Advanced matching settings")
        self.match_advanced_toggle.setCheckable(True)
        self.match_advanced_toggle.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.match_advanced_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        mastering_layout.addWidget(self.match_advanced_toggle)
        self.match_advanced_panel = QtWidgets.QWidget()
        advanced = QtWidgets.QVBoxLayout(self.match_advanced_panel)
        self.smoothing = self.control(advanced, "Smoothing", 0.02, 2, 0.33, " oct", 2)
        self.boost = self.control(advanced, "Maximum boost", 0, 18, 6, " dB", 1)
        self.cut = self.control(advanced, "Maximum cut", 0, 18, 6, " dB", 1)
        mastering_layout.addWidget(self.match_advanced_panel)
        self.match_advanced_panel.hide()
        self.match_advanced_toggle.toggled.connect(self.toggle_match_advanced)
        self.process_button = self.button(
            self.views.action_layout, "Analyse + process", self.process
        )
        self.views.action_layout.addWidget(self.section_workbench.render_button)
        self.views.action_layout.addWidget(self.name_region_button)
        self.views.action_layout.addWidget(self.section_workbench.capture_button)
        self.views.action_layout.addWidget(self.section_workbench.add_button)
        self.views.action_layout.addWidget(self.section_workbench.update_button)
        self.task_actions = [
            self.process_button,
            self.section_workbench.render_button,
            self.name_region_button,
            self.section_workbench.capture_button,
            self.section_workbench.add_button,
            self.section_workbench.update_button,
        ]
        self.views.action_layout.addWidget(self.manual_eq_view.render_button)
        self.task_actions.append(self.manual_eq_view.render_button)
        self.views.action_layout.addWidget(self.compressor_view.render_button)
        self.task_actions.append(self.compressor_view.render_button)
        self.views.action_layout.addWidget(self.output_view.render_button)
        self.task_actions.append(self.output_view.render_button)
        for button in (
            self.clipping_inspector.analyse_button,
            self.clipping_inspector.review_button,
            self.clipping_inspector.repair_button,
            self.clipping_inspector.listen_button,
        ):
            self.views.action_layout.addWidget(button)
            self.task_actions.append(button)
        self.clipping_inspector.review_link = QtWidgets.QToolButton()
        self.clipping_inspector.review_link.setText("Review candidate list")
        self.clipping_inspector.review_link.clicked.connect(self.clipping_inspector.toggle_review)
        self.clipping_inspector.layout().insertWidget(3, self.clipping_inspector.review_link)
        self.process_button.setMinimumHeight(46)
        self.match_subnav = QtWidgets.QWidget()
        match_subnav_layout = QtWidgets.QHBoxLayout(self.match_subnav)
        match_subnav_layout.setContentsMargins(0, 0, 0, 0)
        match_subnav_layout.setSpacing(4)
        for title, key, tip in (
            ("Match", "match", "Whole-song matching settings"),
            ("Targets", "reference", "Capture and manage reference targets"),
            ("Sections", "sections", "Match the mix in named sections"),
        ):
            button = QtWidgets.QPushButton(title)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed
            )
            button.clicked.connect(lambda _=False, k=key: self.views.setStep(k))
            match_subnav_layout.addWidget(button)
            setattr(self, f"match_subnav_{key}", button)
        match_subnav_layout.addStretch(1)
        match_layout.addWidget(self.match_subnav)
        match_layout.addWidget(self.mastering_controls)
        match_layout.addStretch(1)
        for index in range(self.views.stack.count()):
            page = self.views.stack.widget(index)
            for label in page.findChildren(QtWidgets.QLabel):
                label.setWordWrap(True)
                label.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
                )
        for control in (self.amount, self.smoothing, self.boost, self.cut):
            control.valueChanged.connect(self.settings_changed)

        row = QtWidgets.QHBoxLayout()
        self.play_button = self.button(row, "Play", self.play)
        self.button(row, "Stop", self.stop)
        self.ab_button = self.button(row, "Listening: original", self.toggle_ab)
        self.ab_reason = QtWidgets.QLabel("")
        self.ab_reason.setWordWrap(True)
        row.addWidget(self.ab_reason, 1)
        self.preview_mode = QtWidgets.QComboBox()
        for spec in SPECS:
            self.preview_mode.addItem(f"{spec.before_label} / {spec.after_label}", spec.key)
        self.preview_mode.currentIndexChanged.connect(self.preview_mode_changed)
        row.addWidget(self.preview_mode)
        layout.addLayout(row)
        selection_controls = QtWidgets.QHBoxLayout()
        self.play_selection_button = self.button(
            selection_controls, "Play selection", self.play_selection
        )
        self.loop_selection = QtWidgets.QCheckBox("Loop selection")
        self.loop_selection.toggled.connect(self.loop_changed)
        selection_controls.addWidget(self.loop_selection)
        self.whole_song_button = self.button(selection_controls, "Whole song", self.whole_song)
        self.playback_mode = QtWidgets.QLabel("Playback: whole song")
        selection_controls.addWidget(self.playback_mode, 1)
        self.playback_selection_controls = QtWidgets.QWidget()
        self.playback_selection_controls.setLayout(selection_controls)
        layout.addWidget(self.playback_selection_controls)
        transport = QtWidgets.QHBoxLayout()
        self.seek_slider = SeekSlider(QtCore.Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setToolTip("Click or drag to seek. Arrow keys move one second.")
        self.seek_slider.sliderPressed.connect(self.begin_scrub)
        self.seek_slider.sliderReleased.connect(self.end_scrub)
        self.seek_slider.valueChanged.connect(self.seek)
        transport.addWidget(self.seek_slider, 1)
        self.time_label = QtWidgets.QLabel("0:00.0 / 0:00.0")
        self.time_label.setMinimumWidth(160)
        transport.addWidget(self.time_label)
        layout.addLayout(transport)
        self.meters = QtWidgets.QLabel("RMS and sample-peak measurements appear after processing.")
        self.meters.setWordWrap(True)
        self.details_toggle = QtWidgets.QToolButton()
        self.details_toggle.setText("Measurements and status")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        layout.addWidget(self.details_toggle)
        self.details_panel = QtWidgets.QWidget()
        details_layout = QtWidgets.QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.addWidget(self.meters)
        self.note = QtWidgets.QLabel(
            "Preview uses RMS-matched levels and shared headroom. Exports omit preview gain matching."
        )
        self.note.setWordWrap(True)
        details_layout.addWidget(self.note)
        self.status = QtWidgets.QLabel("Ready. FIR: 2049 taps. Offline processing.")
        self.status.setWordWrap(True)
        details_layout.addWidget(self.status)
        layout.addWidget(self.details_panel)
        self.details_panel.hide()
        self.details_toggle.toggled.connect(self.toggle_details)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.check_playback)
        self.timer.start(100)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #10151e; color: #d8e1ed; }
            QLabel { padding: 4px; }
            QPushButton { background: #253246; border: 1px solid #384b64;
                          border-radius: 6px; padding: 6px; }
            QPushButton:hover { border-color: #63dfc0; }
            QPushButton:disabled { color: #647085; }
            QDoubleSpinBox { background: #1c2737; padding: 6px; border: 1px solid #384b64; }
            QSlider::groove:horizontal { background: #253246; height: 6px; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #63dfc0; border-radius: 3px; }
            QSlider::handle:horizontal { background: #d8e1ed; width: 14px;
                                         margin: -5px 0; border-radius: 7px; }
        """)
        self.setup_project_actions()
        self.update_buttons()
        self.update_project_title()
        self.views.currentChanged.connect(self.tab_changed)
        self.section_workbench.target_name.textChanged.connect(self.update_buttons)
        reference_waveform.selection_changed.connect(lambda _: self.update_buttons())
        self.section_workbench.table.itemSelectionChanged.connect(self.update_buttons)
        self.tab_changed()
        self.reset_filter_view()

    def button(self, layout, title, callback):
        button = QtWidgets.QPushButton(title)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def control(self, layout, title, low, high, value, suffix, decimals):
        group = QtWidgets.QVBoxLayout()
        group.addWidget(QtWidgets.QLabel(title))
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(low, high)
        control.setDecimals(decimals)
        control.setSingleStep(1 if decimals == 0 else 0.1)
        control.setValue(value)
        control.setSuffix(suffix)
        group.addWidget(control)
        layout.addLayout(group)
        return control

    def plot(self, label, units, layout):
        plot = pg.PlotWidget(axisItems={"bottom": FrequencyAxis(orientation="bottom")})
        plot.setLogMode(x=True)
        plot.getAxis("bottom").enableAutoSIPrefix(False)
        plot.setLabel("bottom", "Frequency")
        plot.setLabel("left", label, units=units)
        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.setXRange(np.log10(20), np.log10(20000), padding=0)
        plot.getViewBox().setLimits(
            xMin=np.log10(20), xMax=np.log10(20000), maxXRange=3, minXRange=0.05
        )
        plot.disableAutoRange(axis="x")
        layout.addWidget(plot, 1)
        return plot

    def stage_pair(self, stage):
        """One stage's raw input/output and its rendered audible pair.

        Outputs are cached per stage so editing a later stage keeps every earlier
        comparison valid instead of discarding the whole chain.
        """
        if stage == "repair":
            return StagePair(
                self.source[0] if self.source is not None else None,
                self.repair_result.audio if self.repair_result is not None else None,
                self.repair_preview,
            )
        if stage == "match":
            processing = self.processing_source()
            return StagePair(
                processing[0] if processing is not None else None,
                self.match_after if self.mastering_preview is not None else None,
                self.mastering_preview,
            )
        if stage == "eq":
            return StagePair(
                self.eq_before,
                self.eq_after if self.eq_preview is not None else None,
                self.eq_preview,
            )
        if stage == "dynamics":
            return StagePair(
                self.dynamics_before,
                self.dynamics_after if self.dynamics_preview is not None else None,
                self.dynamics_preview,
            )
        return StagePair(
            self.output_before,
            self.output_after if self.output_preview is not None else None,
            self.output_preview,
        )

    def comparison_pairs(self):
        return {stage: self.stage_pair(stage) for stage in STAGES}

    def comparison_side(self):
        if self.audition_mode == ORIGINAL_ONLY:
            return Side.ORIGINAL
        return Side.AFTER if self.listen_processed else Side.BEFORE

    def comparison(self, key=None, side=None):
        """Resolve the selected comparison through the shared model."""
        selected = self.audition_mode if key is None else key
        if side is None:
            side = self.comparison_side() if selected == self.audition_mode else Side.BEFORE
        stage = spec_for(selected).stage
        return resolve(
            selected,
            self.comparison_pairs(),
            side=self.comparison_side() if side is None else side,
            original=self.source[0] if self.source is not None else None,
            fallback=(self.processing_source() or (None,))[0],
            rate=self.source[1] if self.source is not None else 1,
            bypassed={name: item.bypassed for name, item in self.project.stages.items()},
            reason=self.workflow_status().stages[stage].reason if stage else "",
            input_label="repaired input" if self.repair_active else "original",
        )

    def apply_comparison(self, key=None, side=None):
        """Point the transport at whatever the shared model resolved.

        This is the only place that selects a comparison, so the listening side
        and the hidden selector can never disagree with what is audible.
        """
        state = self.comparison(key, side)
        self.audition_mode = state.key
        self.listen_processed = state.side.processed
        self.preview = state.rendered_playback
        if self.preview is None and self.playing:
            # The callback reads one cursor across a pair of arrays, so a stream
            # that is already running must keep a usable pair when a step has
            # nothing rendered to compare yet. Matching a full recording costs a
            # pass over it, so keep the last identity pair rather than rebuilding
            # it on every step change.
            cached = self._identity_playback
            if cached is None or cached[0] != id(state.before):
                cached = (id(state.before), state.playback_arrays)
                self._identity_playback = cached
            self.preview = cached[1]
        with QtCore.QSignalBlocker(self.preview_mode):
            self.preview_mode.setCurrentIndex(self.preview_mode.findData(state.key))
        self.update_ab_label()
        self.update_ab_hint()

    @contextlib.contextmanager
    def shared_workflow_status(self):
        """Resolve workflow status once for a burst of control updates.

        Deriving it costs a config digest, and the comparison model needs a reason
        string from it on every control it refreshes.
        """
        previous = self._workflow_status
        if previous is None:
            self._workflow_status = self.derive_workflow_status()
        try:
            yield self._workflow_status
        finally:
            self._workflow_status = previous

    def workflow_status(self):
        if self._workflow_status is not None:
            return self._workflow_status
        return self.derive_workflow_status()

    def derive_workflow_status(self):
        pairs = self.comparison_pairs()
        rendered = {stage for stage in STAGES if pairs[stage].after is not None}
        return derive_workflow_status(
            self.project,
            source_loaded=self.source is not None,
            repair_unavailable=self.repair_unavailable,
            rendered_stages=rendered,
            final_render_current=self.output is not None and self.chain_result is not None,
            sample_rate=self.source[1] if self.source is not None else 48000,
        )

    def update_buttons(self):
        with self.shared_workflow_status() as workflow:
            self.refresh_controls(workflow)

    def refresh_controls(self, workflow):
        busy = self.worker is not None
        self.load_mix.setEnabled(not busy)
        self.load_ref.setEnabled(not busy)
        self.process_button.setEnabled(
            not busy
            and workflow.analysis_allowed
            and (self.reference is not None or self.project.reference_target is not None)
        )
        self.export_button.setEnabled(not busy and workflow.export_available)
        self.play_button.setEnabled(not busy and self.source is not None)
        self.preview_mode.setEnabled(not busy and self.source is not None)
        for position, spec in enumerate(SPECS):
            stage = spec.stage
            self.preview_mode.model().item(position).setEnabled(
                stage is None or self.comparison(spec.key).available
            )
        self.seek_slider.setEnabled(not busy and self.source is not None)
        self.waveform.setEnabled(not busy and self.source is not None)
        self.region_page.setEnabled(not busy and self.source is not None)
        self.section_workbench.reference_waveform.selection_controls.setEnabled(
            not busy and self.reference is not None
        )
        self.section_workbench.setEnabled(not busy)
        self.section_workbench.reference_page.setEnabled(not busy)
        self.clipping_inspector.setEnabled(not busy and self.source is not None)
        self.section_workbench.capture_button.setEnabled(not busy and self.reference is not None)
        self.section_workbench.render_button.setEnabled(
            not busy and workflow.analysis_allowed and bool(self.section_workbench.sections)
        )
        has_selection = self.source is not None and self.waveform.selection is not None
        self.clipping_inspector.analyse_button.setEnabled(not busy and has_selection)
        self.clipping_inspector.suggest_button.setEnabled(not busy and has_selection)
        self.clipping_inspector.manual_analyse_button.setEnabled(not busy and has_selection)
        self.clipping_inspector.review_button.setEnabled(
            not busy
            and self.clipping_inspector.report is not None
            and bool(self.clipping_inspector.report.candidates)
        )
        self.clipping_inspector.listen_button.setEnabled(
            not busy and has_selection and self.comparison("repair").available
        )
        self.clipping_inspector.repair_button.setEnabled(
            not busy and bool(self.clipping_inspector.checked_intervals())
        )
        self.clipping_inspector.reset_repair_button.setEnabled(
            not busy and self.repair_result is not None
        )
        self.clipping_inspector.export_repair_button.setEnabled(
            not busy and self.repair_result is not None
        )
        self.play_selection_button.setEnabled(not busy and has_selection)
        self.loop_selection.setEnabled(not busy and has_selection)
        self.whole_song_button.setEnabled(not busy and self.source is not None)
        for control in (self.amount, self.smoothing, self.boost, self.cut):
            control.setEnabled(not busy)
        self.clipping_inspector.refresh_actions()
        self.manual_eq_view.setEnabled(not busy and self.source is not None)
        self.manual_eq_view.canvas.setEnabled(not busy and self.source is not None)
        self.manual_eq_view.render_button.setEnabled(not busy and workflow.render_allowed)
        self.manual_eq_view.listen_button.setEnabled(not busy and self.comparison("eq").available)
        self.compressor_view.setEnabled(not busy and self.source is not None)
        self.compressor_view.canvas.setEnabled(not busy and self.source is not None)
        self.compressor_view.render_button.setEnabled(not busy and workflow.render_allowed)
        self.compressor_view.listen_button.setEnabled(
            not busy and self.comparison("dynamics").available
        )
        self.output_view.setEnabled(not busy and self.source is not None)
        self.output_view.canvas.setEnabled(not busy and self.source is not None)
        self.output_view.render_button.setEnabled(not busy and workflow.render_allowed)
        self.output_view.listen_button.setEnabled(
            not busy and self.comparison("output-gain").available
        )
        self.update_project_actions()
        self.update_step_navigation(workflow)
        if not busy:
            self.update_stage_summaries(workflow)
        self.update_ab_hint()
        self.workspace.refresh()
        self.update_action_bar()

    def update_stage_summaries(self, workflow):
        for stage, view in (
            ("eq", self.manual_eq_view),
            ("dynamics", self.compressor_view),
            ("output", self.output_view),
        ):
            status = workflow.stages[stage]
            if not status.comparison_available:
                view.summary.setText(status.reason)

    def update_action_bar(self):
        workflow = self.workflow_status()
        matching = self.views.currentWidget() is self.match_page
        sections = self.views.currentWidget() is self.section_workbench
        regions = self.views.currentWidget() is self.region_page
        reference = self.views.currentWidget() is self.section_workbench.reference_page
        clipping = self.views.currentWidget() is self.clipping_inspector
        manual_eq = self.views.currentWidget() is self.manual_eq_view
        compressor = self.views.currentWidget() is self.compressor_view
        output = self.views.currentWidget() is self.output_view
        self.views.action_bar.show()
        shown = set()
        if matching:
            shown.add(self.process_button)
        elif sections:
            shown.add(self.section_workbench.render_button)
            shown.add(
                self.section_workbench.update_button
                if self.section_workbench.selected_section()
                else self.section_workbench.add_button
            )
        elif regions:
            shown.add(self.name_region_button)
        elif reference:
            shown.add(self.section_workbench.capture_button)
        elif clipping:
            shown.add(self.clipping_inspector.primary_action())
        elif manual_eq:
            shown.add(self.manual_eq_view.render_button)
        elif compressor:
            shown.add(self.compressor_view.render_button)
        elif output:
            shown.add(self.output_view.render_button)
        for button in self.task_actions:
            button.setVisible(button in shown)
        self.name_region_button.setEnabled(
            self.worker is None and self.source is not None and self.waveform.selection is not None
        )
        self.section_workbench.capture_button.setEnabled(
            self.worker is None
            and self.reference is not None
            and self.section_workbench.reference_waveform.selection is not None
            and bool(self.section_workbench.target_name.text().strip())
        )
        self.clipping_inspector.review_link.setVisible(
            self.clipping_inspector.report is not None
            and bool(self.clipping_inspector.report.candidates)
        )
        if self.worker is not None:
            message = self.status.text()
        elif self.source is None:
            message = (
                "Load a mix."
                if self.project.source is None
                else "Relink the original mix to continue."
            )
        elif regions:
            message = (
                "Select a passage and give it a name."
                if self.waveform.selection is None
                else "Selected passage ready to name."
            )
        elif reference:
            message = (
                "Load a reference."
                if self.reference is None
                else (
                    "Select a reference passage."
                    if self.section_workbench.reference_waveform.selection is None
                    else "Name this target, then capture it."
                )
            )
        elif clipping:
            message = self.clipping_inspector.result_heading.text()
        elif manual_eq:
            stage = workflow.stages["eq"]
            message = (
                "EQ rendered. Click the listening button to compare before/after EQ, or export."
                if stage.state is RenderState.READY
                else stage.reason
            )
        elif compressor:
            stage = workflow.stages["dynamics"]
            message = (
                "Compression rendered. Click the listening button to compare, or export."
                if stage.state is RenderState.READY
                else stage.reason
            )
        elif output:
            stage = workflow.stages["output"]
            message = (
                "Output measured. Review sample peaks, compare or export."
                if stage.measurements_available
                else stage.reason
            )
        elif sections:
            count = len(self.section_workbench.sections)
            message = (
                f"{count} sections ready to render."
                if count
                else "Select a mix passage and add a section."
            )
        elif matching:
            stage = workflow.stages["match"]
            if workflow.matching_analysis is AnalysisState.RETAINED and stage.comparison_available:
                message = "Matching rendered with analysis retained from an earlier input."
            elif stage.state is RenderState.READY:
                message = "Matching complete. Compare the result or export."
            elif self.project.match_mode == "none" and not self.project.stages["match"].bypassed:
                message = (
                    "Matching is optional. Add a reference to analyse it; otherwise it passes unchanged."
                    if self.reference is None and self.project.reference_target is None
                    else "Mix and reference ready. Analyse Matching, or leave it unchanged."
                )
            else:
                message = stage.reason
        else:
            message = "Mix and reference ready."
        self.views.action_label.setText(message)

    def toggle_details(self, visible):
        self.details_panel.setVisible(visible)
        if visible:
            self.meters.show()
            self.note.show()
            self.status.show()
        with QtCore.QSignalBlocker(self.workspace.measurements_action):
            self.workspace.measurements_action.setChecked(visible)
        self.details_toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if visible else QtCore.Qt.ArrowType.RightArrow
        )

    def toggle_match_advanced(self, visible):
        self.match_advanced_panel.setVisible(visible)
        self.match_advanced_toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if visible else QtCore.Qt.ArrowType.RightArrow
        )

    def configure_workflow_layout(self):
        if not hasattr(self, "match_advanced_toggle"):
            return
        matching = self.views.currentWidget() is self.match_page
        wide = self.width() >= NARROW_WIDTH
        orientation = QtCore.Qt.Orientation.Horizontal if wide else QtCore.Qt.Orientation.Vertical
        if self.workspace_split.orientation() != orientation:
            self.workspace_split.setOrientation(orientation)
            self.workspace_split.setSizes(
                [max(1, self.width() - 320), 320] if wide else [max(1, self.height() - 250), 175]
            )
        self.views.setMinimumWidth(250 if wide else 0)
        self.views.setMaximumWidth((300 if matching else 340) if wide else 16777215)
        self.views.setMinimumHeight(0 if wide else 155)
        self.views.action_layout.setDirection(
            QtWidgets.QBoxLayout.Direction.TopToBottom
            if wide
            else QtWidgets.QBoxLayout.Direction.LeftToRight
        )
        self.preview_mode.hide()
        self.playback_selection_controls.hide()
        self.details_toggle.hide()
        self.details_panel.setVisible(self.details_toggle.isChecked())
        if self.details_toggle.isChecked():
            self.meters.show()
            self.note.show()
            self.status.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "workspace_split"):
            self.configure_workflow_layout()

    def refresh_file_labels(self):
        if self.views.currentWidget() is self.clipping_inspector or (
            self.views.currentWidget() is self.region_page
            and self.audition_mode in ("original", "repair")
        ):
            self.files.setText(
                f"Recording: {self.source[3]}"
                if self.source
                else "Load a recording and select a passage."
            )
        elif self.source is None and self.reference is None:
            self.files.setText("Load a mix and a reference to begin.")
        else:
            mix = self.source[3] if self.source else "none"
            reference = self.reference[3] if self.reference else "none"
            self.files.setText(f"Mix: {mix}    |    Reference: {reference}")

    def refresh_skip_label(self):
        """Drop the step name when the inspector column cannot afford it."""
        verb = getattr(self, "skip_verb", None)
        if verb is None:
            return
        self.skip_button.setToolTip(f"{verb} {self.skip_step_title}")
        self.skip_button.setText(
            f"{verb} {self.skip_step_title}" if self.width() >= NARROW_WIDTH else verb
        )

    def go_back(self):
        """Move to the previous step. Navigation never renders or bakes audio."""
        step = previous_step(self.views.currentStep())
        if step is not None:
            self.views.setStep(step.key)

    def go_continue(self):
        """Move to the next step once this one is Ready or Skipped."""
        workflow = self.workflow_status()
        status = self.current_step_status(workflow)
        if not status.can_continue:
            # The reason belongs beside the control, not in a hidden status line.
            self.views.action_label.setText(status.reason)
            self.continue_button.setToolTip(status.reason)
            return
        step = next_step(self.views.currentStep())
        if step is not None:
            self.views.setStep(step.key)

    def toggle_skip_step(self):
        """Bypass the current optional stage, keeping its settings for later."""
        step = step_for(self.views.currentStep())
        if step.stage is None:
            self.status.setText("This step cannot be skipped.")
            return
        bypassing = self.project.stages[step.stage].bypassed
        self.set_stage_bypass(step.stage, not bypassing)
        # Skipping advances; turning a stage back on stays put so the change is
        # made where the learner can see it.
        following = None if bypassing else next_step(step.key)
        if following is not None:
            self.views.setStep(following.key)

    def current_step_status(self, workflow):
        """The render state of the step being shown, for Continue and Skip.

        A step with no processing stage of its own is never waiting on a render.
        """
        step = step_for(self.views.currentStep())
        if step.stage is None:
            return READY_STAGE
        return workflow.stages[step.stage]

    def update_step_navigation(self, workflow):
        """Show each step's state on its navigation entry, and control Continue."""
        for step in NAVIGATION:
            index = self.views.indexOfStep(step.key)
            if index < 0:
                continue
            status = workflow.stages[step.stage] if step.stage else None
            if step.key == "export":
                # Export is a step rather than a stage, so name whatever is
                # actually holding it up instead of assuming it is a render.
                status = StageStatus(
                    RenderState.READY if workflow.export_available else RenderState.NEEDS_RENDER,
                    workflow.render_reason,
                    workflow.render_action,
                )
            label = f"{step.title} — {nav_label(status)}" if status is not None else step.title
            if self.views.navigation.tabText(index) != label:
                self.views.navigation.setTabText(index, label)
        step = step_for(self.views.currentStep())
        status = self.current_step_status(workflow)
        busy = self.worker is not None
        self.back_button.setEnabled(not busy and previous_step(step.key) is not None)
        following = next_step(step.key)
        self.continue_button.setEnabled(not busy and status.can_continue and following is not None)
        self.continue_button.setToolTip(
            f"Go to {following.title}." if following is not None else "This is the final step."
        )
        skippable = step.stage is not None and step.skippable
        self.skip_button.setVisible(skippable)
        self.skip_button.setEnabled(not busy and skippable)
        if skippable:
            self.skip_verb = "Enable" if self.project.stages[step.stage].bypassed else "Skip"
            self.skip_step_title = step.title
        else:
            # Nothing to skip here, so leave no stale wording behind.
            self.skip_verb = None
            self.skip_step_title = step.title
            self.skip_button.setText("")
        self.refresh_skip_label()
        for key in ("match", "reference", "sections"):
            button = getattr(self, f"match_subnav_{key}", None)
            if button is not None:
                with QtCore.QSignalBlocker(button):
                    button.setChecked(self.views.currentStep() == key)
        if self.views.currentStep() == "export":
            self.export_view.draw(workflow)

    def tab_changed(self, *args):
        self.workspace.tool_changed()
        clipping = self.views.currentWidget() is self.clipping_inspector
        reviewing = self.views.currentWidget() is self.region_page and self.audition_mode in (
            "original",
            "repair",
        )
        repair_workflow = clipping or reviewing
        self.load_mix.setText("Load recording" if repair_workflow else "Load mix")
        self.mastering_controls.setVisible(not repair_workflow)
        self.load_ref.setVisible(not repair_workflow)
        self.export_button.setVisible(not repair_workflow)
        self.details_toggle.setVisible(not repair_workflow)
        self.details_panel.setVisible(not repair_workflow and self.details_toggle.isChecked())
        self.meters.setVisible(not repair_workflow)
        self.note.setVisible(not repair_workflow)
        self.status.setVisible(
            not repair_workflow
            or (clipping and self.clipping_inspector.advanced_toggle.isChecked())
        )
        self.whole_song_button.setText("Whole recording" if repair_workflow else "Whole song")
        self.refresh_skip_label()
        self.refresh_file_labels()
        if self.source is not None:
            self.workspace.sync_audio()
        self.workspace.refresh()
        self.update_action_bar()
        self.update_step_navigation(self.workflow_status())
        self.configure_workflow_layout()
        if self.views.currentWidget() is self.manual_eq_view:
            self.manual_eq_view.draw_response()
        if self.views.currentWidget() is self.compressor_view:
            self.compressor_view.draw()
        if self.views.currentWidget() is self.output_view:
            self.output_view.draw()

    def start_job(self, function, callback, message, failed=None):
        if self.worker is not None:
            raise RuntimeError("Another operation is already in progress.")
        self.stop()
        self.status.setText(message)
        worker = Worker(function)
        self.worker = worker
        worker.finished.connect(lambda: self.job_finished(worker, callback, failed or self.error))
        self.update_buttons()
        worker.start()

    def job_finished(self, worker, callback, failed):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()
        try:
            if worker.failure is not None:
                failed(worker.failure)
            else:
                callback(worker.value)
        except Exception as error:  # noqa: BLE001 -- callback failures belong at the UI boundary
            self.error(str(error))
        finally:
            self.update_buttons()

    def error(self, message):
        self.status.setText("Operation failed.")
        QtWidgets.QMessageBox.warning(self, "Omazone", message)

    def load(self, target):
        if target == "source" and self.source is None and self.project.source is not None:
            self.relink_recording("source")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load audio", "", "Audio (*.wav *.flac *.aiff *.aif);;All files (*)"
        )
        if path:

            def action():
                self.load_audio_path(target, path)

            if target == "source":
                self.guard_unsaved(action)
            else:
                action()

    def load_audio_path(self, target, path):
        self.start_job(
            lambda: load_audio(path),
            lambda data: self.loaded(target, data),
            "Loading and analysing…",
        )

    def loaded(self, target, data):
        if not self.restoring_project:
            if target == "source":
                self.begin_source_project(data)
            else:
                self.project.reference = data[5] if len(data) > 5 else None
                self.project.reference_target = TargetProfile(
                    "whole-reference",
                    data[3],
                    data[2],
                    data[1],
                    SampleRegion(0, len(data[0])),
                    data[3],
                )
        setattr(self, target, data)
        if target == "source":
            self.repair_result = self.repaired_source = self.repair_preview = None
        self.apply_comparison("mastering", Side.BEFORE)
        self.invalidate(record=False)
        if target == "source":
            self.reset_playback_mode()
            self.position = 0
            index = data[4] if len(data) > 4 else PeakIndex(data[0])
            self.waveform.set_audio(index, data[1])
            self.section_workbench.reset_mix()
            self.clipping_inspector.reset_source()
            self.renderer = None
            self.renderer_key = None
            self.manual_eq_view.restore()
            self.compressor_view.restore()
            self.output_view.restore()
            with QtCore.QSignalBlocker(self.seek_slider):
                self.seek_slider.setRange(0, len(data[0]))
                self.seek_slider.setSingleStep(data[1])
                self.seek_slider.setPageStep(data[1] * 10)
        else:
            self.section_workbench.set_reference(data)
        self.update_transport()
        self.refresh_file_labels()
        self.plot_spectra()
        self.status.setText(
            f"{'Mix' if target == 'source' else 'Reference'} loaded: {data[3]}. Current tool unchanged."
        )
        self.tab_changed()
        if not self.restoring_project:
            self.project_changed()
            self.refresh_named_regions()

    def invalidate(self, record=True):
        if record:
            self.project_changed()
        self.stop()
        self.output = None
        self.section_result = None
        self.mastering_preview = self.match_after = None
        self.eq_preview = self.eq_before = self.eq_after = self.chain_result = None
        self.dynamics_preview = self.dynamics_before = self.dynamics_after = None
        self.output_preview = self.output_before = self.output_after = None
        self.apply_comparison(side=Side.BEFORE)
        self.reset_filter_view()
        self.meters.setText("Process to update measurements.")
        self.update_buttons()

    def invalidate_eq(self):
        self.project_changed()
        self.stop()
        self.output = None
        self.eq_preview = self.eq_after = None
        self.dynamics_preview = None
        self.dynamics_before = self.dynamics_after = None
        self.output_preview = self.output_before = self.output_after = None
        self.chain_result = None
        self.apply_comparison(side=Side.BEFORE)
        self.meters.setText("Render to update measurements.")
        self.workspace.completed_key = None
        self.update_buttons()

    def invalidate_dynamics(self):
        self.project_changed()
        self.stop()
        self.output = None
        self.dynamics_preview = self.dynamics_after = None
        self.output_preview = self.output_before = self.output_after = None
        self.chain_result = None
        self.apply_comparison(side=Side.BEFORE)
        self.meters.setText("Render to update measurements.")
        self.workspace.completed_key = None
        self.update_buttons()

    def invalidate_output(self):
        self.project_changed()
        self.stop()
        self.output = None
        self.output_preview = self.output_after = None
        self.chain_result = None
        self.apply_comparison(side=Side.BEFORE)
        self.meters.setText("Render to update measurements.")
        self.workspace.completed_key = None
        self.update_buttons()

    def get_renderer(self):
        repair = self.repair_result.audio if self.repair_result is not None else None
        key = (id(self.source[0]), id(repair), self.source[1])
        if key != self.renderer_key:
            self.renderer = ChainRenderer(self.source[0], self.source[1], repair)
            self.renderer_key = key
        return self.renderer

    def settings_changed(self):
        self.invalidate()
        self.plot_spectra()
        self.status.setText("Settings changed. Click Analyse + process to render.")

    def plot_spectra(self):
        self.workspace.completed_key = None
        self.spectrum_plot.setTitle("Whole-song spectra")
        self.spectrum_plot.clear()
        for data, name, color in (
            (
                self.processing_source(),
                "Repaired input" if self.repair_active else "Mix",
                "#73a8ff",
            ),
            (self.reference, "Reference", "#eabb6b"),
        ):
            if data:
                spectrum = data[2]
                self.draw_spectrum(spectrum, name, color)
        if self.output is not None:
            self.draw_spectrum(self.output[1], "Processed", "#63dfc0")
        if self.reference is None and self.project.reference_target is not None:
            self.draw_spectrum(
                self.project.reference_target.spectrum, "Saved reference target", "#eabb6b"
            )

    def draw_spectrum(self, spectrum, name, color):
        # Power per Hz is normalised to unit area for comparable displays at
        # different sample rates and FFT bin widths.
        spacing = spectrum.frequency[1] - spectrum.frequency[0]
        density = spectrum.power / max(np.sum(spectrum.power) * spacing, 1e-30)
        self.spectrum_plot.plot(
            spectrum.frequency[1:],
            10 * np.log10(np.maximum(density[1:], 1e-15)),
            pen=pg.mkPen(color, width=1.5),
            name=name,
        )

    def process(self):
        if not self.ensure_processing_ready():
            return
        if self.reference is None and self.project.reference_target is None:
            self.error("Load a reference or open a project with a captured reference spectrum.")
            return
        workflow = self.workflow_status()
        if not workflow.analysis_allowed:
            self.error(workflow.render_reason)
            return
        self.project.match_mode = "whole"
        self.sync_project()
        settings = self.project.matching
        source, rate, spectrum = self.processing_source()[:3]
        reference = self.reference[2] if self.reference else self.project.reference_target.spectrum
        snapshot = copy.deepcopy(self.project)
        renderer = self.get_renderer()

        def calculate():
            spec = design_match(spectrum, reference, rate, settings)
            snapshot.calibration = MatchCalibration(
                snapshot.config_key(), snapshot.input_key(), whole=spec
            )
            result = renderer.render(snapshot)
            return (
                result.output,
                analyse(result.output, rate),
                spec,
                audition_pair(result.repaired, result.matched),
                result,
                audition_pair(result.matched, result.equalized),
                audition_pair(result.equalized, result.pre_output),
                audition_pair(result.pre_output, result.output),
            )

        self.invalidate()
        self.start_job(calculate, self.processed, "Designing filter and rendering blocks…")

    def processed(self, result, learn=True):
        self.output = result[:3]
        self.mastering_preview = self.preview = result[3]
        if len(result) > 4:
            self.chain_result = result[4]
            self.match_after = result[4].matched
            self.eq_before = result[4].matched
            self.eq_after = result[4].equalized
            self.eq_preview = result[5]
            self.dynamics_before = result[4].equalized
            self.dynamics_after = result[4].pre_output
            self.dynamics_preview = result[6]
            self.output_before = result[4].pre_output
            self.output_after = result[4].output
            self.output_preview = result[7]
        self.apply_comparison("mastering", Side.BEFORE)
        self.plot_spectra()
        spec = self.output[2]
        if learn:
            self.project.match_mode = "whole"
            self.record_calibration(whole=spec)
        self.project.needs_render = False
        self.eq_plot.setTitle("Whole-song correction")
        self.draw_filter(spec)

        source = self.processing_source()[0]
        output = self.output[0]
        self.meters.setText(
            f"Input RMS: {rms_db(source):.1f} dBFS  |  Output RMS: {rms_db(output):.1f} dBFS"
            f"  |  Output sample peak: {peak_db(output):.1f} dBFS"
        )
        self.status.setText(
            f"Rendered. FIR delay: {spec.latency_samples} samples "
            f"({1000 * spec.latency_samples / spec.sample_rate:.1f} ms), compensated in file."
        )
        self.workspace.render_completed()
        self.compressor_view.rendered()
        self.output_view.rendered()
        self.focus_rendered_step()

    def focus_rendered_step(self):
        """Compare the stage that was just rendered instead of the raw recording.

        Rendering EQ, compression, or output expresses a wish to hear that step. Without
        this, a viewer left on the original recording kept the listening button
        disabled while the inspector reported a successful render.
        """
        tool = self.views.currentWidget()
        key = {
            id(self.manual_eq_view): "eq",
            id(self.compressor_view): "dynamics",
            id(self.output_view): "output-gain",
        }.get(id(tool))
        if key is not None and self.comparison(key).available:
            self.workspace.focus_comparison(key)

    def draw_filter(self, spec):
        self.eq_plot.clear()
        self.eq_plot.plot(
            spec.frequency[1:],
            spec.requested_db[1:],
            pen=pg.mkPen("#eabb6b", width=2),
            name="Requested",
        )
        frequency, response = spec.response()
        self.eq_plot.plot(
            frequency[1:], response[1:], pen=pg.mkPen("#63dfc0", width=2), name="Actual FIR"
        )

    def reset_filter_view(self):
        self.eq_plot.clear()
        self.eq_plot.setTitle("No correction yet. Analyse to build the filter.")
        self.eq_plot.plot(
            [20, 20000], [0, 0], pen=pg.mkPen("#647085", style=QtCore.Qt.PenStyle.DashLine)
        )
        self.eq_plot.setYRange(-8, 8, padding=0)

    def sections_rendered(self, payload, learn=True):
        result, spectrum, previews = payload[:3]
        output = payload[3].output if len(payload) > 3 else result.audio
        self.processed(
            (output, spectrum, result.curves[0].filter, previews, *payload[3:]), learn=False
        )
        if learn:
            self.project.match_mode = "sections"
            self.record_calibration(sections=result.curves)
        self.section_result = result
        section = self.section_workbench.selected_section()
        curve = next(
            (item for item in result.curves if section and item.section.id == section.id),
            result.curves[0],
        )
        self.show_section_curve(curve, switch_view=False)
        self.workspace.refresh()
        self.status.setText(
            f"Rendered {len(result.curves)} sections with {len(result.transitions)} aligned transition windows. Ready for A/B and export."
        )

    def show_section_curve(self, curve, switch_view=True):
        profile = self.section_workbench.targets[curve.section.target_id]
        self.spectrum_plot.clear()
        self.spectrum_plot.setTitle(f"Section: {curve.section.name} | Target: {profile.name}")
        self.draw_spectrum(curve.source, "Mix section", "#73a8ff")
        self.draw_spectrum(profile.spectrum, "Target", "#eabb6b")
        self.eq_plot.setTitle(f"Correction for {curve.section.name}")
        self.draw_filter(curve.filter)
        if switch_view:
            self.workspace.mode.setCurrentIndex(self.workspace.mode.findData("spectrum"))
        self.status.setText(
            f"Showing section correction: {curve.section.name}. Render sections for the full-song preview."
        )

    def play(self):
        if self.playing:
            self.stop()
            return
        self.stop()
        if self.preview is None:
            self.preview = self.comparison().playback_arrays
        if self.transport.region is not None:
            region = self.transport.region
            if not region.start <= self.position < region.end:
                self.position = region.start
        elif self.position >= len(self.source[0]):
            self.position = 0
        try:
            import sounddevice as sd

            def callback(outdata, frames, time_info, status):
                audio = self.preview[int(self.listen_processed)]
                if self.transport.fill(audio, outdata):
                    self.playing = False
                    raise sd.CallbackStop

            self.stream = sd.OutputStream(
                samplerate=self.source[1],
                channels=self.source[0].shape[1],
                dtype="float32",
                callback=callback,
            )
            self.playing = True
            self.stream.start()
            self.play_button.setText("Pause")
        except Exception as error:  # noqa: BLE001 -- audio backend failures need a UI message
            self.stop()
            self.error(f"Playback unavailable: {error}")

    def stop(self):
        self.selection_timer.stop()
        self.resume_after_selection_edit = False
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.playing = False
        self.play_button.setText("Play")

    def check_playback(self):
        if self.stream is not None and not self.playing:
            self.stop()
        self.update_transport()
        if self.playing and self.views.currentWidget() is self.manual_eq_view:
            if not self.workspace.timer.isActive():
                self.workspace.timer.start()

    def begin_scrub(self):
        self.resume_after_scrub = self.playing
        self.stop()

    def end_scrub(self):
        if self.resume_after_scrub:
            self.resume_after_scrub = False
            if self.source is not None and self.position < len(self.source[0]):
                self.play()

    def seek(self, position):
        if self.source is None:
            return
        resume = self.playing or self.resume_after_selection_edit
        self.stop()
        self.position = min(len(self.source[0]), max(0, position))
        region = self.transport.region
        if region is not None and not region.start <= self.position < region.end:
            self.reset_playback_mode()
        self.update_transport()
        if resume and self.position < len(self.source[0]):
            self.play()

    def update_transport(self):
        if self.source is None and self.project.source is None:
            return
        rate = self.source[1] if self.source else self.project.source.sample_rate
        length = len(self.source[0]) if self.source else self.project.source.frames
        if not self.seek_slider.isSliderDown():
            with QtCore.QSignalBlocker(self.seek_slider):
                self.seek_slider.setValue(self.position)

        def timestamp(samples):
            tenths = round(samples / rate * 10)
            minutes, remainder = divmod(tenths, 600)
            seconds, fraction = divmod(remainder, 10)
            return f"{minutes}:{seconds:02d}.{fraction}"

        self.time_label.setText(f"{timestamp(self.position)} / {timestamp(length)}")
        self.waveform.set_position(self.position)
        self.workspace.update_cursor()

    def toggle_ab(self):
        self.listen_processed = not self.listen_processed
        self.update_ab_label()
        self.workspace.follow_audition()

    def update_ab_label(self):
        self.ab_button.setText(f"Listening: {self.comparison().side_label}")

    def update_ab_hint(self):
        """Explain the comparison beside the control, not only in a tooltip."""
        state = self.comparison()
        if state.side is Side.ORIGINAL:
            reason = (
                "Original recording only. Choose View -> Step input to compare a processing step."
            )
        elif self.worker is not None:
            reason = "Waiting for rendering to finish."
        elif self.source is None:
            reason = "Load a recording first."
        elif not state.aligned:
            reason = "Comparison sides are not the same length, so they cannot be compared."
        elif state.available:
            reason = f"{state.labels[0]} / {state.labels[1]}"
        else:
            reason = state.reason
        self.ab_reason.setText(reason)
        self.ab_button.setToolTip(reason)
        self.ab_button.setEnabled(
            self.worker is None and state.side is not Side.ORIGINAL and state.available
        )

    def preview_mode_changed(self):
        key = self.preview_mode.currentData()
        if key == self.audition_mode:
            return
        resume = self.playing
        self.stop()
        self.apply_comparison(key, Side.BEFORE)
        self.update_buttons()
        self.workspace.follow_audition()
        if resume:
            self.play()

    def processing_source(self):
        return self.repaired_source if self.repair_active else self.source

    @property
    def repair_active(self):
        return self.repaired_source is not None and not self.project.stages["repair"].bypassed

    def repair_applied(self, payload):
        result, spectrum, previews = payload[:3]
        self.clipping_inspector.repair_reported(result)
        if not result.repaired:
            self.status.setText(
                "No intervals passed reconstruction checks. Current audio is unchanged."
            )
            return
        self.repair_result = result
        self.repair_unavailable = False
        if not self.restoring_project and len(payload) > 3:
            self.project.repairs = payload[3]
        self.repaired_source = (
            result.audio,
            self.source[1],
            spectrum,
            self.source[3] + " (repaired)",
        )
        self.repair_preview = previews
        self.invalidate()
        self.apply_comparison("repair", Side.BEFORE)
        self.waveform.set_repair(result)
        self.plot_spectra()
        self.views.setStep("repair")
        self.meters.setText(
            f"Original RMS: {rms_db(self.source[0]):.1f} dBFS | Repaired RMS: {rms_db(result.audio):.1f} dBFS | Repaired sample peak: {peak_db(result.audio):.1f} dBFS"
        )
        self.status.setText(
            f"Reconstructed {len(result.repaired)} intervals. Original/repaired A/B is ready; matching will use the repaired input."
        )
        self.update_buttons()
        self.workspace.render_completed()

    def reset_repair(self):
        if self.repair_result is None:
            return
        self.stop()
        self.repair_result = self.repaired_source = self.repair_preview = None
        self.repair_unavailable = False
        self.project.repairs = []
        self.apply_comparison("mastering", Side.BEFORE)
        self.invalidate()
        self.waveform.set_repair(None)
        self.clipping_inspector.reset_repair_status()
        with QtCore.QSignalBlocker(self.clipping_inspector.table):
            for row in range(len(self.clipping_inspector.rows)):
                self.clipping_inspector.table.item(row, 7).setText("")
                self.clipping_inspector.table.item(row, 7).setToolTip("")
                self.clipping_inspector.table.item(row, 7).setData(
                    QtCore.Qt.ItemDataRole.UserRole, None
                )
        self.plot_spectra()
        self.status.setText("Repair reset. Subsequent matching uses the original input.")
        if self.views.currentStep() == "repair":
            self.apply_comparison(ORIGINAL_ONLY)

    def export_repair(self):
        if self.repair_result is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export repaired audio", "repaired.wav", "WAV (*.wav)"
        )
        if not path:
            return
        if not path.lower().endswith(".wav"):
            path += ".wav"
        audio, rate = self.repair_result.audio, self.source[1]
        self.start_job(
            lambda: sf.write(path, audio, rate, subtype="DOUBLE"),
            lambda _: self.status.setText(f"Exported repaired 64-bit float WAV: {path}"),
            "Exporting repaired audio…",
        )

    @property
    def position(self):
        return self.transport.position

    @position.setter
    def position(self, value):
        self.transport.position = value

    def reset_playback_mode(self):
        self.transport.region = None
        self.transport.loop = False
        with QtCore.QSignalBlocker(self.loop_selection):
            self.loop_selection.setChecked(False)
        self.playback_mode.setText("Playback: whole song")

    def set_region_mode(self, region):
        self.transport.region = region
        self.transport.loop = self.loop_selection.isChecked()
        if not region.start <= self.position < region.end:
            self.position = region.start
        self.playback_mode.setText(
            "Playback: looping selection" if self.transport.loop else "Playback: selection once"
        )

    def play_selection(self):
        region = self.waveform.selection
        if region is None:
            return
        self.stop()
        self.position = region.start
        self.set_region_mode(region)
        self.update_transport()
        self.play()

    def loop_changed(self, enabled):
        region = self.waveform.selection
        resume = self.playing or self.resume_after_selection_edit
        self.stop()
        if region is not None:
            self.set_region_mode(region)
        else:
            self.reset_playback_mode()
        self.update_transport()
        if resume:
            self.play()
        self.workspace.refresh()

    def whole_song(self):
        resume = self.playing or self.resume_after_selection_edit
        self.stop()
        self.reset_playback_mode()
        if resume and self.source is not None and self.position < len(self.source[0]):
            self.play()
        self.workspace.refresh()

    def selection_changed(self, region):
        self.clipping_inspector.selection_changed(region)
        if self.transport.region is not None and self.transport.region != region:
            resume = self.playing or self.resume_after_selection_edit
            self.stop()
            if region is None:
                self.reset_playback_mode()
            else:
                self.set_region_mode(region)
            self.resume_after_selection_edit = resume
            if resume:
                self.selection_timer.start()
            self.update_transport()
        self.update_buttons()

    def finish_selection_edit(self):
        resume = self.resume_after_selection_edit
        self.resume_after_selection_edit = False
        if resume and self.source is not None and self.position < len(self.source[0]):
            self.play()

    def export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export processed audio", "matched.wav", "WAV (*.wav)"
        )
        if not path:
            return
        if not path.lower().endswith(".wav"):
            path += ".wav"
        audio, rate = self.output[0], self.source[1]
        self.start_job(
            lambda: sf.write(path, audio, rate, subtype="FLOAT"),
            lambda _: self.status.setText(f"Exported 32-bit float WAV: {path}"),
            "Exporting…",
        )

    def closeEvent(self, event):
        if getattr(self, "close_approved", False):
            self.stop()
            self.workspace.close_jobs()
            event.accept()
            return
        if self.worker is not None:
            self.status.setText("Wait for the current operation to finish before closing.")
            event.ignore()
            return
        if self.project.dirty:
            event.ignore()
            self.guard_unsaved(self.close_after_approval)
            return
        self.stop()
        self.workspace.close_jobs()
        event.accept()

    def close_after_approval(self):
        self.close_approved = True
        QtCore.QTimer.singleShot(0, self.close)

    def show_waveform(self, signal=None):
        self.views.setStep("listen")
        self.workspace.mode.setCurrentIndex(self.workspace.mode.findData("waveform"))
        if signal is not None:
            self.workspace.signal.setCurrentIndex(self.workspace.signal.findData(signal))


def main():
    parser = argparse.ArgumentParser(
        prog="omazone", description="Omazone spectral matching workbench"
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Create the window, close it, and exit without the GUI loop",
    )
    arguments = parser.parse_args()
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = Window()
    window.show()
    if arguments.selftest:
        app.processEvents()
        window.close()
        print("Omazone selftest OK")
        return
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
