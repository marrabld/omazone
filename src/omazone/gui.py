"""Qt desktop workbench. DSP and file loading run outside the UI thread."""

import sys
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import soundfile as sf
from PySide6 import QtCore, QtWidgets

from .engine import (
    MatchSettings,
    analyse,
    audition_pair,
    design_match,
    peak_db,
    render,
    rms_db,
)
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
        self.worker = None
        self.stream = None
        self.preview = None
        self.position = 0
        self.playing = False
        self.listen_processed = False
        self.resume_after_scrub = False

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
        self.views.addTab(self.waveform, "Waveform / selection")
        layout.addWidget(self.views, 1)

        controls = QtWidgets.QHBoxLayout()
        self.amount = self.control(controls, "Match amount", 0, 100, 50, "%", 0)
        self.smoothing = self.control(controls, "Smoothing", 0.02, 2, 0.33, " oct", 2)
        self.boost = self.control(controls, "Maximum boost", 0, 18, 6, " dB", 1)
        self.cut = self.control(controls, "Maximum cut", 0, 18, 6, " dB", 1)
        layout.addLayout(controls)
        for control in (self.amount, self.smoothing, self.boost, self.cut):
            control.valueChanged.connect(self.settings_changed)

        row = QtWidgets.QHBoxLayout()
        self.process_button = self.button(row, "Analyse + process", self.process)
        self.play_button = self.button(row, "Play", self.play)
        self.button(row, "Stop", self.stop)
        self.ab_button = self.button(row, "Listening: original", self.toggle_ab)
        layout.addLayout(row)
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
            "Preview uses RMS-matched levels and shared headroom. Export keeps the raw EQ result."
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
        self.ab_button.setEnabled(self.output is not None)
        self.seek_slider.setEnabled(not busy and self.source is not None)
        self.waveform.setEnabled(not busy and self.source is not None)
        for control in (self.amount, self.smoothing, self.boost, self.cut):
            control.setEnabled(not busy)

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
        self.invalidate()
        if target == "source":
            self.position = 0
            index = data[4] if len(data) > 4 else PeakIndex(data[0])
            self.waveform.set_audio(index, data[1])
            self.views.setCurrentWidget(self.waveform)
            with QtCore.QSignalBlocker(self.seek_slider):
                self.seek_slider.setRange(0, len(data[0]))
                self.seek_slider.setSingleStep(data[1])
                self.seek_slider.setPageStep(data[1] * 10)
        self.update_transport()
        mix = self.source[3] if self.source else "none"
        reference = self.reference[3] if self.reference else "none"
        self.files.setText(f"Mix: {mix}    |    Reference: {reference}")
        self.plot_spectra()
        self.status.setText("Loaded. Reference sample rate may differ from the mix.")

    def invalidate(self):
        self.stop()
        self.output = None
        self.preview = None
        self.listen_processed = False
        self.ab_button.setText("Listening: original")
        self.eq_plot.clear()
        self.meters.setText("Process to update measurements.")
        self.update_buttons()

    def settings_changed(self):
        self.invalidate()
        self.plot_spectra()
        self.status.setText("Settings changed. Click Analyse + process to render.")

    def plot_spectra(self):
        self.spectrum_plot.clear()
        for data, name, color in (
            (self.source, "Mix", "#73a8ff"),
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
        source, rate, spectrum = self.source[:3]
        reference = self.reference[2]

        def calculate():
            spec = design_match(spectrum, reference, rate, settings)
            output = render(source, spec)
            return output, analyse(output, rate), spec, audition_pair(source, output)

        self.start_job(calculate, self.processed, "Designing filter and rendering blocks…")

    def processed(self, result):
        self.output = result[:3]
        self.preview = result[3]
        self.plot_spectra()
        spec = self.output[2]
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
        source = self.source[0]
        output = self.output[0]
        self.meters.setText(
            f"Input RMS: {rms_db(source):.1f} dBFS  |  Output RMS: {rms_db(output):.1f} dBFS"
            f"  |  Output sample peak: {peak_db(output):.1f} dBFS"
        )
        self.status.setText(
            f"Rendered. FIR delay: {spec.latency_samples} samples "
            f"({1000 * spec.latency_samples / spec.sample_rate:.1f} ms), compensated in file."
        )

    def play(self):
        if self.playing:
            self.stop()
            return
        self.stop()
        if self.preview is None:
            source = self.source[0]
            self.preview = audition_pair(source, source)
        if self.position >= len(self.source[0]):
            self.position = 0
        try:
            import sounddevice as sd

            def callback(outdata, frames, time_info, status):
                audio = self.preview[int(self.listen_processed)]
                # Seeking stops the stream before changing this shared cursor.
                available = min(frames, len(audio) - self.position)
                outdata.fill(0)
                if available > 0:
                    outdata[:available] = audio[self.position : self.position + available]
                self.position += available
                if self.position >= len(audio):
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
        resume = self.playing
        self.stop()
        self.position = min(len(self.source[0]), max(0, position))
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
        self.ab_button.setText(
            "Listening: processed" if self.listen_processed else "Listening: original"
        )

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
