"""Exercise worker handoff, plot updates, and float-WAV export without a display."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import soundfile as sf
from PySide6 import QtWidgets
from scipy import signal

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
