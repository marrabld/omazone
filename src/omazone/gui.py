"""Qt desktop workbench. DSP and file loading run outside the UI thread."""

import argparse
import copy
import math
import sys
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import soundfile as sf
from PySide6 import QtCore, QtGui, QtWidgets

from .clipping_view import ClippingInspector
from .engine import (
    analyse,
    audition_pair,
    design_match,
    peak_db,
    rms_db,
)
from .manual_eq_view import ManualEQView
from .pipeline import ChainRenderer
from .playback import PlaybackCursor
from .project import AudioReference, MatchCalibration, Project
from .project_controller import ProjectController
from .section_view import SectionWorkbench
from .sections import TargetProfile
from .tool_panel import ToolPanel
from .waveform import PeakIndex, SampleRegion
from .waveform_view import WaveformView
from .workspace import SongWorkspace


class Worker(QtCore.QThread):
    result = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function())
        except Exception as error:  # noqa: BLE001 -- surface worker errors at the UI boundary
            self.failed.emit(str(error))


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
        self.match_output = None
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
        self.views.addTab(self.match_page, "Matching")
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
        self.views.addTab(self.region_page, "Regions")
        self.section_workbench = SectionWorkbench(self)
        self.views.addTab(self.section_workbench.reference_page, "Reference targets")
        self.views.addTab(self.section_workbench, "Mix sections")
        self.clipping_inspector = ClippingInspector(self)
        self.views.addTab(self.clipping_inspector, "Clipping inspection")
        self.manual_eq_view = ManualEQView(self)
        self.views.addTab(self.manual_eq_view, "Manual EQ")
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
        self.workspace = SongWorkspace(self, self.waveform, spectra, reference_waveform)
        self.workspace_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.workspace_split.addWidget(self.workspace)
        self.tool_scroll = self.views.scroll_area
        self.views.setMinimumHeight(175)
        self.workspace_split.addWidget(self.views)
        self.workspace_split.setChildrenCollapsible(False)
        self.workspace_split.setSizes([360, 260])
        layout.addWidget(self.workspace_split, 1)
        self.views.layout().removeWidget(self.views.navigation)
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
        match_layout.addWidget(self.mastering_controls)
        match_layout.addStretch(1)
        for page in (
            self.region_page,
            self.section_workbench.reference_page,
            self.section_workbench,
            self.clipping_inspector,
            self.manual_eq_view,
        ):
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
        self.preview_mode = QtWidgets.QComboBox()
        self.preview_mode.addItem("Input / mastered", "mastering")
        self.preview_mode.addItem("Original / repaired", "repair")
        self.preview_mode.addItem("Original recording", "original")
        self.preview_mode.addItem("Before / after manual EQ", "eq")
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

    def update_buttons(self):
        busy = self.worker is not None
        self.load_mix.setEnabled(not busy)
        self.load_ref.setEnabled(not busy)
        self.process_button.setEnabled(
            not busy
            and self.source is not None
            and (self.reference is not None or self.project.reference_target is not None)
        )
        self.export_button.setEnabled(not busy and self.output is not None)
        self.play_button.setEnabled(not busy and self.source is not None)
        self.ab_button.setEnabled(
            not busy
            and self.audition_mode != "original"
            and (
                self.repair_preview is not None
                if self.audition_mode == "repair"
                else (
                    self.eq_preview is not None
                    if self.audition_mode == "eq"
                    else self.mastering_preview is not None
                )
            )
        )
        self.preview_mode.setEnabled(not busy and self.source is not None)
        self.preview_mode.model().item(1).setEnabled(self.repair_preview is not None)
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
            not busy and self.source is not None and bool(self.section_workbench.sections)
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
            not busy and has_selection and self.repair_result is not None
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
        self.manual_eq_view.render_button.setEnabled(not busy and self.source is not None)
        self.manual_eq_view.listen_button.setEnabled(not busy and self.eq_preview is not None)
        self.update_project_actions()
        self.workspace.refresh()
        self.update_action_bar()

    def update_action_bar(self):
        matching = self.views.currentWidget() is self.match_page
        sections = self.views.currentWidget() is self.section_workbench
        regions = self.views.currentWidget() is self.region_page
        reference = self.views.currentWidget() is self.section_workbench.reference_page
        clipping = self.views.currentWidget() is self.clipping_inspector
        manual_eq = self.views.currentWidget() is self.manual_eq_view
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
            message = (
                "EQ rendered. Compare this step or export."
                if self.eq_preview is not None
                else "Adjust the band, then render. Earlier work is retained."
            )
        elif matching and self.reference is None and self.project.reference_target is None:
            message = "Mix loaded. Add a reference."
        elif sections:
            count = len(self.section_workbench.sections)
            message = (
                f"{count} sections ready to render."
                if count
                else "Select a mix passage and add a section."
            )
        elif self.output is not None and self.project.match_mode == "whole":
            message = "Matching complete. Compare the result or export."
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
        wide = self.width() >= 950
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
        self.preview_mode.setVisible(not repair_workflow)
        self.details_toggle.setVisible(not repair_workflow)
        self.details_panel.setVisible(not repair_workflow and self.details_toggle.isChecked())
        self.meters.setVisible(not repair_workflow)
        self.note.setVisible(not repair_workflow)
        self.status.setVisible(
            not repair_workflow
            or (clipping and self.clipping_inspector.advanced_toggle.isChecked())
        )
        self.whole_song_button.setText("Whole recording" if repair_workflow else "Whole song")
        self.refresh_file_labels()
        if self.source is not None:
            self.workspace.sync_audio()
        self.workspace.refresh()
        self.update_action_bar()
        self.configure_workflow_layout()
        if self.views.currentWidget() is self.manual_eq_view:
            self.manual_eq_view.draw_response()

    def start_job(self, function, callback, message):
        self.stop()
        self.status.setText(message)
        self.worker = Worker(function)
        self.worker.result.connect(callback)
        self.worker.failed.connect(self.error)
        self.worker.finished.connect(self.job_finished)
        self.update_buttons()
        self.worker.start()

    def job_finished(self):
        self.worker.deleteLater()
        self.worker = None
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
            self.audition_mode = "mastering"
            with QtCore.QSignalBlocker(self.preview_mode):
                self.preview_mode.setCurrentIndex(0)
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
        self.mastering_preview = None
        self.eq_preview = self.eq_before = self.match_output = self.chain_result = None
        self.preview = self.repair_preview if self.audition_mode == "repair" else None
        self.listen_processed = False
        self.update_ab_label()
        self.reset_filter_view()
        self.meters.setText("Process to update measurements.")
        self.update_buttons()

    def invalidate_eq(self):
        self.project_changed()
        self.stop()
        self.output = None
        self.eq_preview = None
        self.chain_result = None
        self.preview = None if self.audition_mode == "eq" else self.mastering_preview
        self.listen_processed = False
        self.update_ab_label()
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
                audition_pair(result.matched, result.output),
            )

        self.invalidate()
        self.start_job(calculate, self.processed, "Designing filter and rendering blocks…")

    def processed(self, result, learn=True):
        self.output = result[:3]
        self.mastering_preview = self.preview = result[3]
        if len(result) > 4:
            self.chain_result = result[4]
            self.match_output = result[4].matched
            self.eq_before = result[4].matched
            self.eq_preview = result[5]
        self.audition_mode = "mastering"
        with QtCore.QSignalBlocker(self.preview_mode):
            self.preview_mode.setCurrentIndex(0)
        self.listen_processed = False
        self.update_ab_label()
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
            source = (
                self.eq_before
                if self.audition_mode == "eq" and self.eq_before is not None
                else (
                    self.processing_source()[0]
                    if self.audition_mode == "mastering"
                    else self.source[0]
                )
            )
            self.preview = audition_pair(source, source)
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
        if self.audition_mode == "original":
            label = "original"
        elif self.audition_mode == "eq":
            label = "after EQ" if self.listen_processed else "before EQ"
        elif self.audition_mode == "repair":
            label = "repaired" if self.listen_processed else "original"
        else:
            label = (
                "processed"
                if self.listen_processed
                else ("repaired input" if self.repair_active else "original")
            )
        self.ab_button.setText(f"Listening: {label}")

    def preview_mode_changed(self):
        resume = self.playing
        self.stop()
        self.audition_mode = self.preview_mode.currentData()
        self.preview = (
            self.eq_preview
            if self.audition_mode == "eq"
            else self.repair_preview
            if self.audition_mode == "repair"
            else (self.mastering_preview if self.audition_mode == "mastering" else None)
        )
        self.listen_processed = False
        self.update_ab_label()
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
        with QtCore.QSignalBlocker(self.preview_mode):
            self.preview_mode.setCurrentIndex(1)
        self.audition_mode = "repair"
        self.preview = previews
        self.listen_processed = False
        self.update_ab_label()
        self.waveform.set_repair(result)
        self.plot_spectra()
        self.views.setCurrentWidget(self.clipping_inspector)
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
        self.audition_mode = "mastering"
        with QtCore.QSignalBlocker(self.preview_mode):
            self.preview_mode.setCurrentIndex(0)
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
        if self.views.currentWidget() is self.clipping_inspector:
            self.preview_mode.setCurrentIndex(2)

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
        if self.worker is not None:
            self.status.setText("Wait for the current operation to finish before closing.")
            event.ignore()
            return
        self.stop()
        self.workspace.close_jobs()
        event.accept()

    def show_waveform(self, signal=None):
        self.views.setCurrentWidget(self.region_page)
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
