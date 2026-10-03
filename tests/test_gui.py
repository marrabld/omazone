"""Exercise worker handoff, plot updates, and float-WAV export without a display."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import soundfile as sf
from PySide6 import QtCore, QtTest, QtWidgets
from scipy import signal

from omazone.engine import analyse
from omazone.gui import Window, load_audio


def test_load_render_export(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    audio = np.random.default_rng(3).normal(0, 0.1, (16000, 2))
    mix = tmp_path / "mix.wav"
    reference = tmp_path / "reference.wav"
    exported = tmp_path / "export.wav"
    sf.write(mix, audio, 48000, subtype="FLOAT")
    sf.write(reference, signal.lfilter([1], [1, -0.8], audio, axis=0), 48000)

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None, "Worker timed out"
        assert not errors

    try:
        window.show()
        window.loaded("source", load_audio(mix))
        window.loaded("reference", load_audio(reference))
        window.process()
        wait()
        assert window.output[0].shape == audio.shape
        assert len(window.spectrum_plot.listDataItems()) == 3
        assert len(window.eq_plot.listDataItems()) == 2
        window.toggle_ab()
        assert window.listen_processed
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(exported), "")
        )
        window.export()
        wait()
        result, rate = sf.read(exported, always_2d=True)
        assert rate == 48000
        assert sf.info(exported).subtype == "FLOAT"
        np.testing.assert_allclose(result, window.output[0], atol=1e-7)
        window.amount.setValue(75)
        assert window.output is None
        assert not window.export_button.isEnabled()
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_scrub_and_ab_share_cursor_without_audio_device(monkeypatch):
    import sounddevice as sd

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.active = False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 48000
    audio = np.random.default_rng(4).normal(0, 0.1, (rate * 3, 2)).astype(np.float32)
    try:
        assert not window.seek_slider.isEnabled()
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix.wav"))
        window.preview = (audio, audio * 0.5)
        window.output = (audio * 0.5, analyse(audio, rate), None)
        window.update_buttons()
        window.show()
        app.processEvents()
        window.seek_slider.setValue(rate)
        assert window.position == rate
        assert window.time_label.text() == "0:01.0 / 0:03.0"
        window.play()
        block = np.empty((128, 2), dtype=np.float32)
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, audio[rate : rate + 128])
        window.toggle_ab()
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, audio[rate + 128 : rate + 256] * 0.5)

        # Real mouse events exercise click-to-seek, stop-before-seek, and resume.
        old_stream = window.stream
        slider = window.seek_slider
        point = QtCore.QPoint(slider.width() * 3 // 4, slider.height() // 2)
        QtTest.QTest.mousePress(slider, QtCore.Qt.MouseButton.LeftButton, pos=point)
        assert not window.playing
        assert not old_stream.active
        QtTest.QTest.mouseRelease(slider, QtCore.Qt.MouseButton.LeftButton, pos=point)
        assert window.playing
        assert window.listen_processed
        start = window.position
        assert abs(start - len(audio) * 0.75) < rate * 0.1
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, audio[start : start + 128] * 0.5)

        window.check_playback()
        assert window.seek_slider.value() == window.position
        window.play()  # Pause retains cursor.
        assert not window.playing
        assert window.position == start + 128
        window.seek_slider.setValue(len(audio))
        window.play()
        assert window.position == 0
        window.stop()
        window.loaded("source", (audio[:rate], rate, analyse(audio[:rate], rate), "short.wav"))
        assert window.position == 0
        assert window.seek_slider.maximum() == rate
    finally:
        window.close()
