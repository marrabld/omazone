"""Explicit final gain, sample-peak context and output-stage comparison."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .limiter import LimiterSettings, limiter_parameters
from .limiter import settings_from_parameters as limiter_settings
from .limiter import validate_settings as validate_limiter
from .loudness import format_lufs
from .output_gain import (
    OutputGainSettings,
    output_parameters,
    sample_peak,
    settings_from_parameters,
    validate_settings,
)
from .truepeak import format_true_peak


class SamplePeakCanvas(pg.PlotWidget):
    def __init__(self):
        super().__init__()
        self.setTitle("Sample peaks | source, pre-output and final export")
        self.setLabel("left", "Sample peak", units="dBFS")
        self.getAxis("bottom").setTicks([[(1, "Source"), (2, "Before gain"), (3, "Export")]])
        self.setXRange(0.45, 3.55, padding=0)
        self.setYRange(-60, 6, padding=0)
        self.getViewBox().setMouseEnabled(x=False, y=False)
        self.showGrid(x=False, y=True, alpha=0.15)
        self.addItem(
            pg.InfiniteLine(
                pos=0, angle=0, pen=pg.mkPen("#ff9086", style=QtCore.Qt.PenStyle.DashLine)
            )
        )
        self.bars = []
        self.labels = []
        for x in (1, 2, 3):
            bar = pg.BarGraphItem(
                x=[x],
                y0=-60,
                height=[0],
                width=0.55,
                brush=pg.mkBrush("#647085"),
                pen=pg.mkPen("#647085"),
            )
            self.addItem(bar)
            self.bars.append(bar)
            label = pg.TextItem("", anchor=(0.5, 1), color="#d8e1ed")
            self.addItem(label)
            self.labels.append(label)

    def show_peaks(self, peaks):
        valid = [20 * np.log10(peak) for peak in peaks if peak is not None and peak > 0]
        self.setYRange(-60, max(6, max(valid, default=0) + 10), padding=0)
        for bar, label, x, peak in zip(self.bars, self.labels, (1, 2, 3), peaks, strict=True):
            value = 20 * np.log10(peak) if peak is not None and peak > 0 else float("-inf")
            colour = (
                "#647085"
                if peak is None
                else "#ff9086"
                if peak > 1
                else "#73a8ff"
                if x == 1
                else "#eabb6b"
                if x == 2
                else "#63dfc0"
            )
            bar.setOpts(
                height=max(0, value + 60) if peak is not None else 0,
                brush=pg.mkBrush(colour),
                pen=pg.mkPen(colour),
            )
            label.setText(
                "--" if peak is None else "silence" if peak == 0 else f"{value:+.1f} dBFS"
            )
            label.setPos(x, max(-56, value + 2) if peak is not None else -56)


class OutputCanvas(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.peaks = SamplePeakCanvas()
        layout.addWidget(self.peaks, 2)
        self.reduction = pg.PlotWidget()
        self.reduction.setTitle("Final limiter gain reduction")
        self.reduction.setToolTip(
            "Negative dB of attenuation, including the reported whole-file true-peak safety correction."
        )
        self.reduction.setLabel("left", "Reduction", units="dB")
        self.reduction.setLabel("bottom", "Song time", units="s")
        self.reduction.showGrid(x=True, y=True, alpha=0.12)
        self.reduction.getViewBox().setMouseEnabled(x=True, y=False)
        self.reduction_curve = self.reduction.plot(pen=pg.mkPen("#eabb6b", width=2))
        self.cursor = pg.InfiniteLine(pos=0, pen=pg.mkPen(216, 225, 237, 100))
        self.reduction.addItem(self.cursor)
        layout.addWidget(self.reduction, 1)
        self.reduction.hide()
        self.current_limiting = None

    def show_peaks(self, peaks):
        self.peaks.show_peaks(peaks)

    def show_limiter(self, result):
        if result is self.current_limiting:
            return
        self.current_limiting = result
        self.reduction.setVisible(result is not None)
        if result is None:
            self.reduction_curve.clear()
        else:
            self.reduction_curve.setData(result.time, result.reduction_db)
            self.reduction.setYRange(min(-6, -result.max_reduction_db - 2), 0, padding=0)
            self.reduction.setXRange(0, max(0.1, result.time[-1]), padding=0)

    def set_position(self, seconds):
        self.cursor.setValue(seconds)


class OutputView(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.canvas = OutputCanvas()
        self.restoring = False
        self.cached_source = None
        self.source_peak = None
        self.measured_result = None
        self.before_peak = self.final_peak = None
        self.limiter_input_peak = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        prompt = QtWidgets.QLabel(
            "Set output gain, optionally limit final peaks, then render and compare. "
            "The limiter follows gain and never adds makeup gain. Red means sample "
            "or measured true peaks exceed full scale."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        layout.addWidget(QtWidgets.QLabel("Output gain"))
        self.gain = QtWidgets.QDoubleSpinBox()
        self.gain.setRange(-36, 18)
        self.gain.setDecimals(1)
        self.gain.setSuffix(" dB")
        layout.addWidget(self.gain)
        self.enabled = QtWidgets.QCheckBox("Apply output gain")
        layout.addWidget(self.enabled)
        self.limiter_enabled = QtWidgets.QCheckBox("Enable final limiter")
        self.limiter_enabled.setToolTip(
            "Limit after output gain. An 8x peak check may apply a reported, "
            "whole-file safety attenuation. Original audio and output-gain choices are unchanged."
        )
        layout.addWidget(self.limiter_enabled)
        layout.addWidget(QtWidgets.QLabel("Limiter ceiling"))
        self.ceiling = QtWidgets.QDoubleSpinBox()
        self.ceiling.setRange(-24, 0)
        self.ceiling.setDecimals(1)
        self.ceiling.setSuffix(" dBTP")
        self.ceiling.setValue(-1)
        layout.addWidget(self.ceiling)
        self.timing_toggle = QtWidgets.QToolButton()
        self.timing_toggle.setText("Limiter timing")
        self.timing_toggle.setCheckable(True)
        layout.addWidget(self.timing_toggle)
        self.timing = QtWidgets.QWidget()
        timing_layout = QtWidgets.QVBoxLayout(self.timing)
        timing_layout.addWidget(QtWidgets.QLabel("Lookahead"))
        self.lookahead = QtWidgets.QDoubleSpinBox()
        self.lookahead.setRange(1, 20)
        self.lookahead.setDecimals(1)
        self.lookahead.setSuffix(" ms")
        self.lookahead.setValue(5)
        timing_layout.addWidget(self.lookahead)
        timing_layout.addWidget(QtWidgets.QLabel("Limiter release"))
        self.release = QtWidgets.QDoubleSpinBox()
        self.release.setRange(10, 2000)
        self.release.setDecimals(1)
        self.release.setSuffix(" ms")
        self.release.setValue(100)
        timing_layout.addWidget(self.release)
        layout.addWidget(self.timing)
        self.timing.hide()
        self.timing_toggle.toggled.connect(self.timing.setVisible)
        self.comparison_selector = QtWidgets.QComboBox()
        self.comparison_selector.addItem("Compare output gain", "output-gain")
        self.comparison_selector.addItem("Compare final limiter", "limiter")
        layout.addWidget(self.comparison_selector)
        self.comparison_selector.currentIndexChanged.connect(self.comparison_changed)
        self.listen_button = QtWidgets.QPushButton("Loop and compare output gain")
        self.listen_button.clicked.connect(self.listen)
        layout.addWidget(self.listen_button)
        self.readouts = [QtWidgets.QLabel() for _ in range(3)]
        for label in self.readouts:
            label.setWordWrap(True)
            layout.addWidget(label)
        self.limiter_input_readout = QtWidgets.QLabel()
        self.limiter_input_readout.setWordWrap(True)
        layout.addWidget(self.limiter_input_readout)
        self.summary = QtWidgets.QLabel(
            "Render the full song to check final peaks. Limiting is optional and starts disabled."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.render_button = QtWidgets.QPushButton("Render output")
        self.render_button.clicked.connect(owner.render_saved_recipe)
        self.gain.valueChanged.connect(self.gain_changed)
        self.enabled.toggled.connect(self.changed)
        self.limiter_enabled.toggled.connect(self.limiter_changed)
        for control in (self.ceiling, self.lookahead, self.release):
            control.valueChanged.connect(self.limiter_control_changed)
        self.restore()

    def settings(self):
        return OutputGainSettings(self.gain.value())

    @property
    def comparison_key(self):
        return self.comparison_selector.currentData()

    def limiting_settings(self):
        return LimiterSettings(self.ceiling.value(), self.lookahead.value(), self.release.value())

    def comparison_changed(self, *args):
        self.listen_button.setText(
            "Loop and compare limiter"
            if self.comparison_key == "limiter"
            else "Loop and compare output gain"
        )
        if not self.restoring and self.owner.views.currentWidget() is self:
            self.owner.workspace.sync_audio()
            self.owner.update_buttons()

    def restore(self):
        self.restoring = True
        try:
            try:
                settings = settings_from_parameters(self.owner.project.stages["output"].parameters)
                validate_settings(settings)
                self.summary.setText("Output choices restored. Render to measure the final file.")
            except (TypeError, ValueError) as error:
                settings = OutputGainSettings()
                self.summary.setText(str(error))
            with QtCore.QSignalBlocker(self.gain):
                self.gain.setValue(settings.gain_db)
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(not self.owner.project.stages["output"].bypassed)
            try:
                limiting = limiter_settings(self.owner.project.stages["limiter"].parameters)
                validate_limiter(limiting)
            except (TypeError, ValueError) as error:
                limiting = LimiterSettings()
                self.summary.setText(str(error))
            for control, value in (
                (self.ceiling, limiting.ceiling_db),
                (self.lookahead, limiting.lookahead_ms),
                (self.release, limiting.release_ms),
            ):
                with QtCore.QSignalBlocker(control):
                    control.setValue(value)
            with QtCore.QSignalBlocker(self.limiter_enabled):
                self.limiter_enabled.setChecked(not self.owner.project.stages["limiter"].bypassed)
        finally:
            self.restoring = False
        self.draw()

    def limiter_control_changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        try:
            validate_limiter(limiter_settings(self.owner.project.stages["limiter"].parameters))
        except (TypeError, ValueError):
            self.restore()
            self.summary.setText(
                "Saved limiter settings are unsupported. Choices retained; controls cannot replace them silently."
            )
            return
        with QtCore.QSignalBlocker(self.limiter_enabled):
            self.limiter_enabled.setChecked(True)
        self.limiter_changed()

    def limiter_changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        stage = self.owner.project.stages["limiter"]
        try:
            validate_limiter(limiter_settings(stage.parameters))
        except (TypeError, ValueError):
            # Unknown versioned choices can be bypassed, never silently replaced.
            pass
        else:
            stage.parameters = limiter_parameters(self.limiting_settings())
        stage.bypassed = not self.limiter_enabled.isChecked()
        if not stage.bypassed:
            with QtCore.QSignalBlocker(self.comparison_selector):
                self.comparison_selector.setCurrentIndex(1)
        self.owner.invalidate_limiter()
        self.comparison_changed()
        self.summary.setText(self.owner.workflow_status().stages["limiter"].reason)

    def gain_changed(self, value):
        if not self.restoring and not self.supported_recipe():
            with QtCore.QSignalBlocker(self.gain):
                self.gain.setValue(0)
            self.summary.setText(
                "Saved output settings are unsupported. Choices retained; gain cannot replace them silently."
            )
            return
        if not self.restoring and value != 0 and not self.enabled.isChecked():
            with QtCore.QSignalBlocker(self.enabled):
                self.enabled.setChecked(True)
        self.changed()

    def supported_recipe(self):
        try:
            validate_settings(
                settings_from_parameters(self.owner.project.stages["output"].parameters)
            )
            return True
        except (TypeError, ValueError):
            return False

    def changed(self, *args):
        if self.restoring or self.owner.restoring_project:
            return
        stage = self.owner.project.stages["output"]
        if self.supported_recipe():
            stage.parameters = output_parameters(self.settings())
        stage.bypassed = not self.enabled.isChecked()
        self.owner.invalidate_output()
        self.summary.setText(self.owner.workflow_status().stages["output"].reason)

    @staticmethod
    def reading(name, peak, lufs, intersample):
        """One line per stage: sample peak, true peak, loudness.

        Three measurements that answer three different questions, kept in one
        line so they can be compared, and labelled distinctly because a sample
        peak can read safe while the true peak says otherwise.
        """
        if peak is None:
            return f"{name}: render to measure"
        level = 20 * np.log10(peak) if peak > 0 else None
        parts = [format_lufs(lufs)]
        if intersample is None:
            parts.append(format_true_peak(None))
        else:
            parts.append(format_true_peak(intersample))
            if intersample > 0:
                parts[-1] += " OVER"
        if level is None:
            return f"{name}: silence | " + " | ".join(parts)
        return (
            f"{name}: {level:+.1f} dBFS sample peak"
            + (" | SAMPLE OVER" if peak > 1 else "")
            + " | "
            + " | ".join(parts)
        )

    def draw(self):
        source = self.owner.source[0] if self.owner.source is not None else None
        if source is not self.cached_source:
            self.cached_source = source
            self.source_peak = sample_peak(source) if source is not None else None
        status = self.owner.workflow_status().stages["output"]
        result = self.owner.chain_result if status.measurements_available else None
        self.canvas.show_limiter(result.limiting if result is not None else None)
        if result is not self.measured_result:
            self.measured_result = result
            self.before_peak = sample_peak(result.pre_output) if result is not None else None
            self.final_peak = sample_peak(result.output) if result is not None else None
            self.limiter_input_peak = (
                sample_peak(result.pre_limiter)
                if result is not None and result.pre_limiter is not None
                else None
            )
        peaks = (self.source_peak, self.before_peak, self.final_peak)
        self.canvas.show_peaks(peaks)
        # The render already measured every prefix; never measure on the UI thread.
        source_audio = self.owner.source[0] if self.owner.source is not None else None
        measured = result.loudness if result is not None else {}
        loudness = (
            measured.get(id(audio)) if audio is not None else None
            for audio in (
                source_audio,
                result.pre_output if result else None,
                result.output if result else None,
            )
        )
        # The render already measured these; never measure on the UI thread.
        intersamples = (
            result.true_peak.get(id(audio)) if result is not None and audio is not None else None
            for audio in (
                source_audio,
                result.pre_output if result else None,
                result.output if result else None,
            )
        )
        for label, name, peak, loud, intersample in zip(
            self.readouts,
            ("Original source", "Before output gain", "Final export"),
            peaks,
            loudness,
            intersamples,
            strict=True,
        ):
            label.setText(self.reading(name, peak, loud, intersample))
            over = (peak is not None and peak > 1) or (intersample is not None and intersample > 0)
            label.setStyleSheet("color: #ff9086;" if over else "")
        self.limiter_input_readout.setVisible(self.limiter_enabled.isChecked())
        before_limiter = result.pre_limiter if result is not None else None
        self.limiter_input_readout.setText(
            self.reading(
                "Before limiter",
                self.limiter_input_peak,
                measured.get(id(before_limiter)),
                result.true_peak.get(id(before_limiter)) if result is not None else None,
            )
            if self.limiter_enabled.isChecked()
            else ""
        )

    def rendered(self):
        self.draw()
        result = self.owner.chain_result
        if result is None:
            return
        final = sample_peak(result.output)
        true = result.true_peak.get(id(result.output))
        if result.limiting is not None:
            limiting = result.limiting
            rate = self.owner.source[1]
            peak_text = (
                f"{limiting.reconstructed_peak_db:+.2f} dBTP"
                if limiting.reconstructed_peak_db is not None
                else "silence"
            )
            self.summary.setText(
                f"Limiter rendered. Maximum reduction {limiting.max_reduction_db:.1f} dB. "
                f"Lookahead {limiting.latency_samples} samples "
                f"({1000 * limiting.latency_samples / rate:.1f} ms), compensated in export and A/B. "
                f"8x verified peak {peak_text}. Whole-file true-peak safety attenuation "
                f"{limiting.guard_reduction_db:.2f} dB, included in the reduction trace."
                + (
                    " Earlier source overload may still contain flattened peaks."
                    if self.source_peak is not None and self.source_peak > 1
                    else ""
                )
                + (
                    " Warning: final peaks still exceed full scale."
                    if final > 1 or (true is not None and true > 0)
                    else " Compare or export."
                )
            )
        elif final > 1 or (true is not None and true > 0):
            self.summary.setText(
                "Final sample or true peaks exceed full scale. Lower output gain or enable the final limiter."
            )
        elif self.source_peak is not None and self.source_peak > 1:
            self.summary.setText(
                "Final samples fit below 0 dBFS. Earlier source overload may still contain flattened peaks."
            )
        else:
            self.summary.setText(
                "Final sample peaks fit below 0 dBFS. "
                + (
                    "Measured true peaks also fit below 0 dBTP. Compare or export."
                    if true is not None
                    else "True peak is unavailable for silence or very short audio."
                )
            )

    def listen(self):
        if not self.owner.comparison(self.comparison_key).available:
            return
        self.owner.apply_comparison(self.comparison_key)
        if self.owner.waveform.selection:
            self.owner.play_selection()
        else:
            self.owner.play()
