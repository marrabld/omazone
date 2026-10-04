"""Qt desktop workbench. DSP and file loading run outside the UI thread."""

import sys
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import soundfile as sf
from PySide6 import QtCore, QtWidgets

from .clipping_view import ClippingInspector
from .engine import (
    MatchSettings,
    analyse,
    audition_pair,
    design_match,
    peak_db,
    render,
    rms_db,
)
from .playback import PlaybackCursor
from .section_view import SectionWorkbench
from .waveform import PeakIndex
from .waveform_view import WaveformView


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
    return audio, rate, analyse(audio, rate), Path(path).name, PeakIndex(audio)


class Window(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Omazone | spectral matching playground")
        self.resize(1120, 800)
        self.source = self.reference = self.output = None
        self.section_result = None
        self.repair_result = None
        self.repaired_source = None
        self.repair_preview = None
        self.mastering_preview = None
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
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QtWidgets.QLabel("OMAZONE   /   spectral laboratory")
        heading.setStyleSheet("font-size: 24px; font-weight: bold; color: #63dfc0;")
        layout.addWidget(heading)
        self.files = QtWidgets.QLabel("Load a mix and a reference to begin.")
        layout.addWidget(self.files)

        row = QtWidgets.QHBoxLayout()
        self.load_mix = self.button(row, "Load mix", lambda: self.load("source"))
        self.load_ref = self.button(row, "Load reference", lambda: self.load("reference"))
        self.export_button = self.button(row, "Export WAV", self.export)
        layout.addLayout(row)

        pg.setConfigOptions(antialias=True, background="#141a24", foreground="#b8c4d6")
        self.views = QtWidgets.QTabWidget()
        spectra = QtWidgets.QWidget()
        spectra_layout = QtWidgets.QVBoxLayout(spectra)
        spectra_layout.setContentsMargins(0, 0, 0, 0)
        self.spectrum_plot = self.plot("Relative spectral power", "dB", spectra_layout)
        self.spectrum_plot.addLegend()
        self.eq_plot = self.plot("Filter gain", "dB", spectra_layout)
        self.eq_plot.addLegend()
        self.eq_plot.setYRange(-8, 8)
        self.views.addTab(spectra, "Spectrum / EQ")
        self.waveform = WaveformView()
        self.waveform.seek_requested.connect(self.seek)
        self.waveform.selection_changed.connect(self.selection_changed)
        self.selection_timer = QtCore.QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.setInterval(150)
        self.selection_timer.timeout.connect(self.finish_selection_edit)
        self.views.addTab(self.waveform, "Waveform / selection")
        self.section_workbench = SectionWorkbench(self)
        self.views.addTab(self.section_workbench.reference_page, "Reference targets")
        self.views.addTab(self.section_workbench, "Mix sections")
        self.clipping_inspector = ClippingInspector(self)
        self.views.addTab(self.clipping_inspector, "Clipping inspection")
        layout.addWidget(self.views, 1)

        self.mastering_controls = QtWidgets.QWidget()
        mastering_layout = QtWidgets.QVBoxLayout(self.mastering_controls)
        mastering_layout.setContentsMargins(0, 0, 0, 0)
        mastering_layout.addWidget(
            QtWidgets.QLabel("Whole-song matching controls; mix sections have their own settings.")
        )
        controls = QtWidgets.QHBoxLayout()
        self.amount = self.control(controls, "Match amount", 0, 100, 50, "%", 0)
        self.smoothing = self.control(controls, "Smoothing", 0.02, 2, 0.33, " oct", 2)
        self.boost = self.control(controls, "Maximum boost", 0, 18, 6, " dB", 1)
        self.cut = self.control(controls, "Maximum cut", 0, 18, 6, " dB", 1)
        mastering_layout.addLayout(controls)
        process_row = QtWidgets.QHBoxLayout()
        self.process_button = self.button(process_row, "Analyse + process", self.process)
        mastering_layout.addLayout(process_row)
        layout.addWidget(self.mastering_controls)
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
        layout.addLayout(selection_controls)
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
        layout.addWidget(self.meters)
        self.note = QtWidgets.QLabel(
            "Preview uses RMS-matched levels and shared headroom. Exports omit preview gain matching."
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.status = QtWidgets.QLabel("Ready. FIR: 2049 taps. Offline processing.")
        layout.addWidget(self.status)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.check_playback)
        self.timer.start(100)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #10151e; color: #d8e1ed; }
            QLabel { padding: 4px; }
            QPushButton { background: #253246; border: 1px solid #384b64;
                          border-radius: 6px; padding: 10px; }
            QPushButton:hover { border-color: #63dfc0; }
            QPushButton:disabled { color: #647085; }
            QDoubleSpinBox { background: #1c2737; padding: 8px; border: 1px solid #384b64; }
            QSlider::groove:horizontal { background: #253246; height: 6px; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #63dfc0; border-radius: 3px; }
            QSlider::handle:horizontal { background: #d8e1ed; width: 14px;
                                         margin: -5px 0; border-radius: 7px; }
        """)
        self.update_buttons()
        self.views.currentChanged.connect(self.tab_changed)
        self.tab_changed()

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
        plot = pg.PlotWidget()
        plot.setLogMode(x=True)
        plot.setLabel("bottom", "Frequency", units="Hz")
        plot.setLabel("left", label, units=units)
        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.setXRange(np.log10(20), np.log10(20000))
        layout.addWidget(plot, 1)
        return plot

    def update_buttons(self):
        busy = self.worker is not None
        self.load_mix.setEnabled(not busy)
        self.load_ref.setEnabled(not busy)
        self.process_button.setEnabled(
            not busy and self.source is not None and self.reference is not None
        )
        self.export_button.setEnabled(not busy and self.output is not None)
        self.play_button.setEnabled(not busy and self.source is not None)
        self.ab_button.setEnabled(
            not busy
            and self.audition_mode != "original"
            and (
                self.repair_preview is not None
                if self.audition_mode == "repair"
                else self.output is not None
            )
        )
        self.preview_mode.setEnabled(not busy and self.source is not None)
        self.preview_mode.model().item(1).setEnabled(self.repair_preview is not None)
        self.seek_slider.setEnabled(not busy and self.source is not None)
        self.waveform.setEnabled(not busy and self.source is not None)
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

    def refresh_file_labels(self):
        if self.views.currentWidget() is self.clipping_inspector or (
            self.views.currentWidget() is self.waveform
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
        clipping = self.views.currentWidget() is self.clipping_inspector
        reviewing = self.views.currentWidget() is self.waveform and self.audition_mode in (
            "original",
            "repair",
        )
        repair_workflow = clipping or reviewing
        self.load_mix.setText("Load recording" if repair_workflow else "Load mix")
        self.mastering_controls.setVisible(not repair_workflow)
        self.load_ref.setVisible(not repair_workflow)
        self.export_button.setVisible(not repair_workflow)
        self.preview_mode.setVisible(not repair_workflow)
        self.meters.setVisible(not repair_workflow)
        self.note.setVisible(not repair_workflow)
        self.status.setVisible(
            not repair_workflow
            or (clipping and self.clipping_inspector.advanced_toggle.isChecked())
        )
        self.whole_song_button.setText("Whole recording" if repair_workflow else "Whole song")
        self.refresh_file_labels()
        if clipping and self.source is not None:
            self.preview_mode.setCurrentIndex(1 if self.repair_preview is not None else 2)

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
        setattr(self, target, data)
        if target == "source":
            self.repair_result = self.repaired_source = self.repair_preview = None
            self.audition_mode = "mastering"
            with QtCore.QSignalBlocker(self.preview_mode):
                self.preview_mode.setCurrentIndex(0)
        self.invalidate()
        if target == "source":
            self.reset_playback_mode()
            self.position = 0
            index = data[4] if len(data) > 4 else PeakIndex(data[0])
            self.waveform.set_audio(index, data[1])
            self.section_workbench.reset_mix()
            self.clipping_inspector.reset_source()
            self.views.setCurrentWidget(self.waveform)
            with QtCore.QSignalBlocker(self.seek_slider):
                self.seek_slider.setRange(0, len(data[0]))
                self.seek_slider.setSingleStep(data[1])
                self.seek_slider.setPageStep(data[1] * 10)
        else:
            self.section_workbench.set_reference(data)
            self.views.setCurrentWidget(self.section_workbench.reference_page)
        self.update_transport()
        self.refresh_file_labels()
        self.plot_spectra()
        self.status.setText("Loaded. Reference sample rate may differ from the mix.")
        self.tab_changed()

    def invalidate(self):
        self.stop()
        self.output = None
        self.section_result = None
        self.mastering_preview = None
        self.preview = self.repair_preview if self.audition_mode == "repair" else None
        self.listen_processed = False
        self.update_ab_label()
        self.eq_plot.clear()
        self.meters.setText("Process to update measurements.")
        self.update_buttons()

    def settings_changed(self):
        self.invalidate()
        self.plot_spectra()
        self.status.setText("Settings changed. Click Analyse + process to render.")

    def plot_spectra(self):
        self.spectrum_plot.setTitle("Whole-song spectra")
        self.spectrum_plot.clear()
        for data, name, color in (
            (
                self.processing_source(),
                "Repaired input" if self.repaired_source is not None else "Mix",
                "#73a8ff",
            ),
            (self.reference, "Reference", "#eabb6b"),
        ):
            if data:
                spectrum = data[2]
                self.draw_spectrum(spectrum, name, color)
        if self.output is not None:
            self.draw_spectrum(self.output[1], "Processed", "#63dfc0")

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
        settings = MatchSettings(
            amount=self.amount.value() / 100,
            smoothing_octaves=self.smoothing.value(),
            max_boost_db=self.boost.value(),
            max_cut_db=self.cut.value(),
        )
        source, rate, spectrum = self.processing_source()[:3]
        reference = self.reference[2]

        def calculate():
            spec = design_match(spectrum, reference, rate, settings)
            output = render(source, spec)
            return output, analyse(output, rate), spec, audition_pair(source, output)

        self.invalidate()
        self.start_job(calculate, self.processed, "Designing filter and rendering blocks…")

    def processed(self, result):
        self.output = result[:3]
        self.mastering_preview = self.preview = result[3]
        self.audition_mode = "mastering"
        with QtCore.QSignalBlocker(self.preview_mode):
            self.preview_mode.setCurrentIndex(0)
        self.listen_processed = False
        self.update_ab_label()
        self.views.setCurrentIndex(0)
        self.plot_spectra()
        spec = self.output[2]
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

    def sections_rendered(self, payload):
        result, spectrum, previews = payload
        self.processed((result.audio, spectrum, result.curves[0].filter, previews))
        self.section_result = result
        section = self.section_workbench.selected_section()
        curve = next(
            (item for item in result.curves if section and item.section.id == section.id),
            result.curves[0],
        )
        self.show_section_curve(curve)
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
            self.views.setCurrentIndex(0)
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
                self.processing_source()[0] if self.audition_mode == "mastering" else self.source[0]
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
        if self.source is None:
            return
        rate = self.source[1]
        if not self.seek_slider.isSliderDown():
            with QtCore.QSignalBlocker(self.seek_slider):
                self.seek_slider.setValue(self.position)

        def timestamp(samples):
            tenths = round(samples / rate * 10)
            minutes, remainder = divmod(tenths, 600)
            seconds, fraction = divmod(remainder, 10)
            return f"{minutes}:{seconds:02d}.{fraction}"

        self.time_label.setText(f"{timestamp(self.position)} / {timestamp(len(self.source[0]))}")
        self.waveform.set_position(self.position)

    def toggle_ab(self):
        self.listen_processed = not self.listen_processed
        self.update_ab_label()

    def update_ab_label(self):
        if self.audition_mode == "original":
            label = "original"
        elif self.audition_mode == "repair":
            label = "repaired" if self.listen_processed else "original"
        else:
            label = (
                "processed"
                if self.listen_processed
                else ("repaired input" if self.repaired_source is not None else "original")
            )
        self.ab_button.setText(f"Listening: {label}")

    def preview_mode_changed(self):
        resume = self.playing
        self.stop()
        self.audition_mode = self.preview_mode.currentData()
        self.preview = (
            self.repair_preview if self.audition_mode == "repair" else self.mastering_preview
        )
        self.listen_processed = False
        self.update_ab_label()
        self.update_buttons()
        if resume:
            self.play()

    def processing_source(self):
        return self.repaired_source if self.repaired_source is not None else self.source

    def repair_applied(self, payload):
        result, spectrum, previews = payload
        self.clipping_inspector.repair_reported(result)
        if not result.repaired:
            self.status.setText(
                "No intervals passed reconstruction checks. Current audio is unchanged."
            )
            return
        self.repair_result = result
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

    def reset_repair(self):
        if self.repair_result is None:
            return
        self.stop()
        self.repair_result = self.repaired_source = self.repair_preview = None
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

    def whole_song(self):
        resume = self.playing or self.resume_after_selection_edit
        self.stop()
        self.reset_playback_mode()
        if resume and self.source is not None and self.position < len(self.source[0]):
            self.play()

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
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
