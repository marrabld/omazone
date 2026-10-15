"""Compressor inspector and plots for the static curve and rendered gain history."""

from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets
from scipy import signal

from .compressor import (
    CompressorSettings,
    gain_reduction_db,
)
from .dynamics import (
    default_settings,
    dynamics_parameters,
    settings_from_parameters,
    validate_settings,
)
from .engine import peak_db
from .multiband import BAND_NAMES, BandSettings, MultibandResult, crossover_filters

BAND_COLOURS = ("#eabb6b", "#63dfc0", "#73a8ff")


class CompressorCanvas(QtWidgets.QWidget):
    crossovers_changed = QtCore.Signal(float, float)

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.crossover = pg.PlotWidget()
        self.crossover.setTitle("Three complementary linear-phase bands")
        self.crossover.setLabel("left", "Band response", units="dB")
        self.crossover.setLabel("bottom", "Frequency", units="Hz")
        self.crossover.getAxis("bottom").enableAutoSIPrefix(False)
        self.crossover.setLogMode(x=True)
        self.crossover.setYRange(-80, 3, padding=0)
        self.crossover.getViewBox().setMouseEnabled(x=False, y=False)
        self.crossover.showGrid(x=True, y=True, alpha=0.12)
        self.crossover.addLegend()
        self.band_curves = [
            self.crossover.plot(pen=pg.mkPen(colour, width=2), name=name)
            for name, colour in zip(BAND_NAMES, BAND_COLOURS, strict=True)
        ]
        self.crossovers = [pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#d8e1ed")) for _ in range(2)]
        for line in self.crossovers:
            self.crossover.addItem(line)
            line.sigPositionChangeFinished.connect(self.crossover_dragged)
        layout.addWidget(self.crossover, 2)
        self.crossover.hide()
        self.crossover_key = None
        self.transfer = pg.PlotWidget()
        self.transfer.setLabel("left", "Output level", units="dBFS")
        self.transfer.setLabel("bottom", "Input level", units="dBFS")
        self.transfer.setTitle("Compression curve | input to output")
        self.transfer.showGrid(x=True, y=True, alpha=0.12)
        self.transfer.setXRange(-60, 6, padding=0)
        self.transfer.setYRange(-60, 6, padding=0)
        self.transfer.getViewBox().setMouseEnabled(x=False, y=False)
        self.transfer.plot(
            [-60, 6], [-60, 6], pen=pg.mkPen("#647085", style=QtCore.Qt.PenStyle.DashLine)
        )
        self.curve = self.transfer.plot(pen=pg.mkPen("#63dfc0", width=3))
        self.threshold = pg.InfiniteLine(
            angle=90, pen=pg.mkPen("#eabb6b", style=QtCore.Qt.PenStyle.DashLine)
        )
        self.transfer.addItem(self.threshold)
        layout.addWidget(self.transfer, 2)
        self.detector = pg.PlotWidget()
        self.detector.setTitle("Linked detector envelope | dBFS")
        self.detector.setLabel("left", "Detector", units="dBFS")
        self.detector.showGrid(x=True, y=True, alpha=0.12)
        self.detector_curve = self.detector.plot(pen=pg.mkPen("#73a8ff", width=1.5))
        self.detector_threshold = pg.InfiniteLine(angle=0, pos=-18, pen=pg.mkPen("#eabb6b", style=QtCore.Qt.PenStyle.DashLine))
        self.detector.addItem(self.detector_threshold)
        self.detector_threshold.hide()
        layout.addWidget(self.detector, 1)
        self.reduction = pg.PlotWidget()
        self.reduction.setTitle("Gain reduction | negative dB")
        self.reduction.setLabel("left", "Reduction", units="dB")
        self.reduction.setLabel("bottom", "Song time", units="s")
        self.reduction.showGrid(x=True, y=True, alpha=0.12)
        self.reduction.setYRange(-18, 0, padding=0)
        self.reduction_curve = self.reduction.plot(pen=pg.mkPen("#eabb6b", width=2))
        self.band_reduction_curves = [self.reduction_curve, *[
            self.reduction.plot(pen=pg.mkPen(colour, width=1.5)) for colour in BAND_COLOURS[1:]
        ]]
        self.band_legend = self.reduction.addLegend(colCount=3, offset=(10, 5))
        for curve, name in zip(self.band_reduction_curves, BAND_NAMES, strict=True):
            self.band_legend.addItem(curve, name)
        self.band_legend.hide()
        self.reduction.setXLink(self.detector)
        layout.addWidget(self.reduction, 1)
        self.cursors = []
        for plot in (self.detector, self.reduction):
            cursor = pg.InfiniteLine(pos=0, pen=pg.mkPen(216, 225, 237, 100))
            plot.addItem(cursor)
            self.cursors.append(cursor)
        self.current_result = None
        self.result_band = None
        for plot in (self.detector, self.reduction):
            plot.getViewBox().setMouseEnabled(x=True, y=False)
        self.setMinimumHeight(270)

    def show_settings(self, settings, bypassed):
        self.detector_threshold.setValue(settings.threshold_db)
        level = np.linspace(-60, 6, 500)
        reduction = gain_reduction_db(level, settings)
        self.curve.setData(level, level if bypassed else level + reduction + settings.makeup_db)
        self.curve.setPen(pg.mkPen("#647085" if bypassed else "#63dfc0", width=3))
        self.threshold.setValue(settings.threshold_db)
        self.transfer.setYRange(
            min(-60, -60 + settings.makeup_db), max(6, 6 + settings.makeup_db), padding=0
        )
        self.transfer.setTitle(
            "Compression bypassed" if bypassed else "Compression curve | input to output"
        )

    def crossover_dragged(self, *args):
        self.crossovers_changed.emit(*(10 ** line.value() for line in self.crossovers))

    def show_crossovers(self, settings, rate, selected):
        self.crossover.setVisible(settings is not None)
        self.transfer.setVisible(settings is None)
        self.detector_threshold.setVisible(settings is not None)
        if settings is None:
            return
        key = (settings.low_hz, settings.high_hz, settings.taps, rate)
        if key != self.crossover_key:
            filters = crossover_filters(settings, rate)
            for curve, coefficients in zip(self.band_curves, filters, strict=True):
                frequency, response = signal.freqz(coefficients, worN=4096, fs=rate)
                curve.setData(frequency[1:], 20 * np.log10(np.maximum(np.abs(response[1:]), 1e-8)))
            self.crossover_key = key
        maximum = min(20000, rate * 0.45)
        ticks = [20, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 20000]
        self.crossover.getAxis("bottom").setTicks([[
            (np.log10(value), f"{value / 1000:g}k" if value >= 1000 else str(value))
            for value in ticks if value <= maximum
        ]])
        self.crossover.setXRange(np.log10(20), np.log10(maximum), padding=0)
        self.crossovers[0].setBounds((np.log10(20), np.log10(settings.high_hz - 0.1)))
        self.crossovers[1].setBounds((np.log10(settings.low_hz + 0.1), np.log10(maximum)))
        for line, frequency in zip(self.crossovers, (settings.low_hz, settings.high_hz), strict=True):
            with QtCore.QSignalBlocker(line):
                line.setValue(np.log10(frequency))
        for index, curve in enumerate(self.band_curves):
            curve.setPen(pg.mkPen(BAND_COLOURS[index], width=3 if index == selected else 1.5))

    def show_result(self, result, selected=0):
        if result is self.current_result and selected == self.result_band:
            return
        self.current_result = result
        self.result_band = selected
        if result is None:
            self.detector_curve.clear()
            for curve in self.band_reduction_curves:
                curve.clear()
            self.band_legend.hide()
            return
        multiband = isinstance(result, MultibandResult)
        self.detector_curve.setPen(pg.mkPen(BAND_COLOURS[selected] if multiband else "#73a8ff", width=1.5))
        self.band_legend.setVisible(multiband)
        self.detector_curve.setData(result.time, result.detector_db[:, selected] if multiband else result.detector_db)
        self.detector.setTitle(f"{BAND_NAMES[selected]} band detector | dBFS" if multiband else "Linked detector envelope | dBFS")
        if multiband:
            for index, curve in enumerate(self.band_reduction_curves):
                curve.setData(result.time, result.reduction_db[:, index])
                curve.setPen(pg.mkPen(BAND_COLOURS[index], width=3 if index == selected else 1.5))
        else:
            self.reduction_curve.setData(result.time, result.reduction_db)
            self.reduction_curve.setPen(pg.mkPen("#eabb6b", width=2))
            for curve in self.band_reduction_curves[1:]:
                curve.clear()
        self.reduction.setYRange(min(-18, float(np.min(result.reduction_db)) - 2), 0, padding=0)
        end = max(0.1, result.time[-1])
        self.detector.setXRange(0, end, padding=0)

    def set_position(self, seconds):
        for cursor in self.cursors:
            cursor.setValue(seconds)


class CompressorView(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.restoring = False
        self.recipe = default_settings()
        self.known_recipe = True
        self.canvas = CompressorCanvas()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        prompt = QtWidgets.QLabel(
            "Choose a mode, enable compression, adjust and render the full song. "
            "Rendering takes time; activity and elapsed time appear above the settings."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Broadband compression", "broadband")
        self.mode.addItem("Three-band compression", "three-band")
        layout.addWidget(self.mode)
        self.band_controls = QtWidgets.QWidget()
        bands = QtWidgets.QVBoxLayout(self.band_controls)
        bands.setContentsMargins(0, 0, 0, 0)
        self.band_selector = QtWidgets.QComboBox()
        self.band_selector.addItems(BAND_NAMES)
        bands.addWidget(QtWidgets.QLabel("Selected band"))
        bands.addWidget(self.band_selector)
        self.band_bypass = QtWidgets.QCheckBox("Bypass selected band's compression")
        self.band_bypass.setToolTip("Keep this band audible but do not compress it or apply its makeup gain.")
        bands.addWidget(self.band_bypass)
        self.solo = QtWidgets.QCheckBox("Solo selected band (audition only)")
        self.solo.setToolTip("Listen to this rendered band only. Solo never changes export or the saved processing recipe.")
        bands.addWidget(self.solo)
        self.crossover_toggle = QtWidgets.QToolButton()
        self.crossover_toggle.setText("Crossover frequencies")
        self.crossover_toggle.setCheckable(True)
        bands.addWidget(self.crossover_toggle)
        self.crossover_controls = QtWidgets.QWidget()
        crossover_layout = QtWidgets.QVBoxLayout(self.crossover_controls)
        self.low_crossover = self.spin(crossover_layout, "Low / mid crossover", 20, 20000, 250, " Hz", 1)
        self.high_crossover = self.spin(crossover_layout, "Mid / high crossover", 20, 20000, 2500, " Hz", 1)
        bands.addWidget(self.crossover_controls)
        self.crossover_controls.hide()
        self.crossover_toggle.toggled.connect(self.crossover_controls.setVisible)
        self.latency_label = QtWidgets.QLabel()
        self.latency_label.setWordWrap(True)
        bands.addWidget(self.latency_label)
        layout.addWidget(self.band_controls)
        self.band_controls.hide()
        # The window places this above the scrolling settings so activity stays
        # visible even when the user has scrolled down to the listening controls.
        self.render_activity = QtWidgets.QWidget()
        activity_layout = QtWidgets.QVBoxLayout(self.render_activity)
        activity_layout.setContentsMargins(4, 4, 4, 4)
        self.render_message = QtWidgets.QLabel()
        self.render_message.setWordWrap(True)
        self.render_message.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        activity_layout.addWidget(self.render_message)
        self.render_progress = QtWidgets.QProgressBar()
        self.render_progress.setRange(0, 0)
        self.render_progress.setTextVisible(False)
        self.render_progress.setAccessibleName("Rendering in progress")
        activity_layout.addWidget(self.render_progress)
        self.render_activity.hide()
        self.rendering = False
        self.render_elapsed = QtCore.QElapsedTimer()
        self.render_timer = QtCore.QTimer(self)
        self.render_timer.setInterval(1000)
        self.render_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.render_timer.timeout.connect(self.update_render_message)
        self.threshold = self.spin(layout, "Threshold", -60, 0, -18, " dBFS", 1)
        self.ratio = self.spin(layout, "Ratio", 1, 20, 2, ":1", 1)
        self.makeup = self.spin(layout, "Manual makeup gain", -18, 18, 0, " dB", 1)
        self.advanced_toggle = QtWidgets.QToolButton()
        self.advanced_toggle.setText("Timing, knee and detector")
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced = QtWidgets.QWidget()
        more = QtWidgets.QVBoxLayout(self.advanced)
        self.attack = self.spin(more, "Attack", 0, 200, 10, " ms", 1)
        self.release = self.spin(more, "Release", 1, 3000, 120, " ms", 1)
        self.knee = self.spin(more, "Soft knee", 0, 24, 6, " dB", 1)
        more.addWidget(QtWidgets.QLabel("Stereo-linked detector"))
        self.detector = QtWidgets.QComboBox()
        self.detector.addItem("Peak (largest channel sample)", "peak")
        self.detector.addItem("RMS (channel energy)", "rms")
        more.addWidget(self.detector)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.enabled = QtWidgets.QCheckBox("Enable compressor in the chain")
        self.enabled.setToolTip(
            "Checked: the next render includes compression. Unchecked: it bypasses compression. "
            "This switch does not render audio."
        )
        layout.insertWidget(layout.indexOf(self.mode) + 1, self.enabled)
        self.listen_button = QtWidgets.QPushButton("Loop and compare compression")
        self.listen_button.setToolTip(
            "Before is the chain after EQ, without compression. After adds compression. "
            "Both sides use preview-only loudness matching and shared headroom. "
            "Use View > Original recording to hear the untouched source."
        )
        self.listen_button.clicked.connect(self.listen)
        layout.addWidget(self.listen_button)
        self.summary = QtWidgets.QLabel(
            "Compressor skipped. Set the controls and render to hear it."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.render_button = QtWidgets.QPushButton("Render compression")
        self.render_button.setToolTip(
            "Rebuild the chain with the current settings. Earlier matching and EQ are retained. "
            "Compression never overwrites its input or the original file."
        )
        self.render_button.clicked.connect(owner.render_saved_recipe)
        for control in (
            self.threshold,
            self.ratio,
            self.makeup,
            self.attack,
            self.release,
            self.knee,
        ):
            control.valueChanged.connect(self.changed)
        self.detector.currentIndexChanged.connect(self.changed)
        self.enabled.toggled.connect(self.changed)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.band_selector.currentIndexChanged.connect(self.band_selected)
        self.band_bypass.toggled.connect(self.changed)
        self.solo.toggled.connect(self.solo_changed)
        self.low_crossover.valueChanged.connect(self.crossovers_changed)
        self.high_crossover.valueChanged.connect(self.crossovers_changed)
        self.canvas.crossovers_changed.connect(self.crossovers_dragged)
        self.restore()

    def set_rendering(self, busy):
        # Timer delivery is not the render's lifetime: a delayed/stopped timer
        # must not rebase the clock when another UI refresh catches up.
        if busy and not self.rendering:
            self.render_elapsed.start()
        self.rendering = busy
        if busy:
            if not self.render_timer.isActive():
                self.render_timer.start()
            self.update_render_message()
        else:
            self.render_timer.stop()
            self.render_elapsed.invalidate()
        self.render_activity.setVisible(
            busy and self.owner.views.currentWidget() in (self, self.owner.output_view)
        )

    def update_render_message(self):
        if not self.rendering or not self.render_elapsed.isValid():
            return
        seconds = self.render_elapsed.elapsed() // 1000
        message = (
            f"Processing audio, {seconds}s elapsed. The full song is processed before "
            "comparison is ready. Longer songs can take a while."
        )
        if message != self.render_message.text():
            self.render_message.setText(message)

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
        return CompressorSettings(
            self.threshold.value(),
            self.ratio.value(),
            self.attack.value(),
            self.release.value(),
            self.knee.value(),
            self.makeup.value(),
            self.detector.currentData(),
        )

    @property
    def sample_rate(self):
        return self.owner.source[1] if self.owner.source is not None else 48000

    @property
    def multiband_mode(self):
        return self.mode.currentData() == "three-band"

    @property
    def comparison_key(self):
        return "band-solo" if self.solo.isChecked() and self.solo_pair() is not None else "dynamics"

    def solo_pair(self):
        result = self.owner.chain_result
        if self.multiband_mode and result is not None and isinstance(result.compression, MultibandResult) and self.owner.dynamics_preview is not None:
            index = self.band_selector.currentIndex()
            return result.compression.solo_audio[index], result.compression.solo_playback[index]
        return None

    def clear_solo(self):
        with QtCore.QSignalBlocker(self.solo):
            self.solo.setChecked(False)
        if self.owner.audition_mode == "band-solo" and hasattr(self.owner, "workspace"):
            self.owner.apply_comparison("dynamics")

    def restore_controls(self):
        settings = self.recipe.multiband.bands[self.band_selector.currentIndex()].compressor if self.multiband_mode else self.recipe.broadband
        for control, value in (
            (self.threshold, settings.threshold_db), (self.ratio, settings.ratio),
            (self.attack, settings.attack_ms), (self.release, settings.release_ms),
            (self.knee, settings.knee_db), (self.makeup, settings.makeup_db),
        ):
            with QtCore.QSignalBlocker(control):
                control.setValue(value)
        with QtCore.QSignalBlocker(self.detector):
            self.detector.setCurrentIndex(self.detector.findData(settings.detector))
        with QtCore.QSignalBlocker(self.band_bypass):
            self.band_bypass.setChecked(self.recipe.multiband.bands[self.band_selector.currentIndex()].bypassed)
        for control, value in ((self.low_crossover, self.recipe.multiband.low_hz), (self.high_crossover, self.recipe.multiband.high_hz)):
            with QtCore.QSignalBlocker(control):
                control.setMaximum(min(20000, self.sample_rate * 0.45))
                control.setValue(value)
        self.band_controls.setVisible(self.multiband_mode)

    def restore(self):
        self.restoring = True
        try:
            self.clear_solo()
            try:
                self.recipe = settings_from_parameters(
                    self.owner.project.stages["dynamics"].parameters, self.sample_rate
                )
                validate_settings(self.recipe, self.sample_rate)
                self.known_recipe = True
                self.summary.setText("Compressor settings restored. Render to hear this step.")
            except (TypeError, ValueError) as error:
                self.recipe = default_settings(self.sample_rate)
                self.known_recipe = False
                self.summary.setText(str(error))
            with QtCore.QSignalBlocker(self.mode):
                self.mode.setCurrentIndex(self.mode.findData(self.recipe.mode))
            self.restore_controls()
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(not self.owner.project.stages["dynamics"].bypassed)
        finally:
            self.restoring = False
        self.draw()

    def mode_changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        if not self.known_recipe:
            self.restore()
            return
        self.clear_solo()
        self.recipe = replace(self.recipe, mode=self.mode.currentData())
        self.restore_controls()
        self.save_choices()

    def band_selected(self, *args):
        self.restore_controls()
        self.draw()
        if self.solo.isChecked():
            self.solo_changed()

    def crossovers_changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        low, high = self.low_crossover.value(), self.high_crossover.value()
        if low >= high:
            with QtCore.QSignalBlocker(self.low_crossover):
                self.low_crossover.setValue(self.recipe.multiband.low_hz)
            with QtCore.QSignalBlocker(self.high_crossover):
                self.high_crossover.setValue(self.recipe.multiband.high_hz)
            self.summary.setText("Keep the low crossover below the high crossover.")
            return
        self.changed()

    def crossovers_dragged(self, low, high):
        with QtCore.QSignalBlocker(self.low_crossover):
            self.low_crossover.setValue(low)
        with QtCore.QSignalBlocker(self.high_crossover):
            self.high_crossover.setValue(high)
        self.crossovers_changed()

    def save_choices(self):
        self.clear_solo()
        stage = self.owner.project.stages["dynamics"]
        if self.known_recipe:
            stage.parameters = dynamics_parameters(self.recipe)
        stage.bypassed = not self.enabled.isChecked()
        self.owner.invalidate_dynamics()
        self.draw()
        self.summary.setText(self.owner.workflow_status().stages["dynamics"].reason)

    def solo_changed(self, *args):
        if self.restoring or self.owner.source is None:
            return
        resume = self.owner.playing
        self.owner.stop()
        self.owner.apply_comparison(self.comparison_key)
        self.owner.workspace.follow_audition()
        self.owner.update_buttons()
        if resume:
            self.owner.play()

    def changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        if not self.known_recipe and self.sender() is not self.enabled:
            self.restore()
            self.summary.setText("Unsupported dynamics choices retained; controls cannot replace them silently.")
            return
        if self.multiband_mode:
            bands = list(self.recipe.multiband.bands)
            bands[self.band_selector.currentIndex()] = BandSettings(self.settings(), self.band_bypass.isChecked())
            multiband = replace(self.recipe.multiband, low_hz=self.low_crossover.value(), high_hz=self.high_crossover.value(), bands=tuple(bands))
            self.recipe = replace(self.recipe, multiband=multiband)
        else:
            self.recipe = replace(self.recipe, broadband=self.settings())
        self.save_choices()

    def draw(self):
        self.render_button.setText(
            "Render compression" if self.enabled.isChecked() else "Render without compression"
        )
        bypassed = self.owner.project.stages["dynamics"].bypassed
        self.canvas.show_settings(self.settings(), bypassed or (self.multiband_mode and self.band_bypass.isChecked()))
        if self.multiband_mode:
            self.canvas.transfer.setTitle(f"{BAND_NAMES[self.band_selector.currentIndex()]} band compression curve")
        self.canvas.show_crossovers(self.recipe.multiband if self.multiband_mode else None, self.sample_rate, self.band_selector.currentIndex())
        delay = (self.recipe.multiband.taps - 1) // 2
        self.latency_label.setText(
            f"Bands start at 1:1. Increase the selected ratio to compress. "
            f"FIR {delay} samples ({1000 * delay / self.sample_rate:.1f} ms), compensated. "
            "Unequal gains can expose pre-ringing."
        )
        self.canvas.show_result(
            self.owner.chain_result.compression
            if self.owner.chain_result is not None and self.owner.dynamics_preview is not None
            else None,
            self.band_selector.currentIndex(),
        )
        self.solo.setEnabled(self.solo_pair() is not None and self.owner.worker is None)
        self.canvas.detector.setTitle(
            f"{BAND_NAMES[self.band_selector.currentIndex()]} band detector | dBFS"
            if self.multiband_mode else "Linked detector envelope | dBFS"
        )
        self.listen_button.setText("Loop and play band solo" if self.comparison_key == "band-solo" else "Loop and compare compression")

    def rendered(self):
        result = self.owner.chain_result
        self.draw()
        if result is None or result.compression is None:
            self.summary.setText("Compressor skipped. Export uses the earlier chain.")
            return
        deepest = result.compression.max_reduction_db
        peak = peak_db(result.pre_output)
        if isinstance(result.compression, MultibandResult):
            detail = ", ".join(f"{name} {value:.1f} dB" for name, value in zip(BAND_NAMES, result.compression.band_max_reduction_db, strict=True))
            self.summary.setText(f"Three-band compression rendered. Maximum reductions: {detail}. Output sample peak {peak:.1f} dBFS. FIR delay compensated. Solo is audition-only; export always includes all three bands." + (" Check final limiting: band processing can introduce new peaks." if peak > 0 else " Compare or export."))
            return
        self.summary.setText(
            f"Maximum reduction: {deepest:.1f} dB. Output sample peak: {peak:.1f} dBFS."
            + (
                " Compression output exceeds 0 dBFS; check final output gain and limiting."
                if peak > 0
                else " Compare this step or export."
            )
        )

    def listen(self):
        if self.comparison_key != "band-solo" and not self.owner.comparison("dynamics").available:
            return
        self.owner.apply_comparison(self.comparison_key)
        if self.owner.waveform.selection:
            self.owner.play_selection()
        else:
            self.owner.play()
