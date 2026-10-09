"""Capture compact matching controls after loading generated mix/reference audio.

Run: uv run python tools/render_matching_ready_demo.py docs/images
"""

import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
from PySide6 import QtWidgets
from scipy import signal

from omazone.engine import analyse
from omazone.gui import Window


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = Window()
    rate = 16000
    audio = np.random.default_rng(96).normal(0, 0.12, (rate * 2, 2))
    reference = signal.sosfilt(signal.butter(2, 2500, fs=rate, output="sos"), audio, axis=0)
    try:
        window.resize(1024, 768)
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated mix.wav"))
        window.loaded(
            "reference", (reference, rate, analyse(reference, rate), "Generated reference.wav")
        )

        def wait():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                app.processEvents()
                if (
                    window.worker is None
                    and window.workspace.job is None
                    and not window.workspace.timer.isActive()
                ):
                    break
                time.sleep(0.01)
            else:
                raise RuntimeError("Matching demo timed out.")
            for _ in range(10):
                app.processEvents()
                time.sleep(0.01)

        wait()
        assert window.views.currentStep() == "match", "processing returns to Matching"
        assert window.process_button.isVisible() and window.process_button.isEnabled()
        if not window.grab().save(str(directory / "omazone-matching-ready.png")):
            raise RuntimeError("Could not save screenshot.")
        window.resize(1280, 900)
        window.process()
        wait()
        if not window.grab().save(str(directory / "omazone-matching-plots.png")):
            raise RuntimeError("Could not save processed screenshot.")
        print("Captured plot-focused Matching at 1024x768 and 1280x900.")
    finally:
        window.close()


if __name__ == "__main__":
    main()
