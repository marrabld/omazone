"""Capture output-gain and sample-peak views from generated float audio.

uv run python tools/render_output_demo.py docs/images
"""

import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
from PySide6 import QtWidgets

from omazone.engine import analyse
from omazone.gui import Window


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = Window()
    window.resize(1280, 900)
    errors = []
    window.error = errors.append
    rate = 16000
    t = np.arange(rate * 4) / rate
    envelope = np.where(t < 1.0, 0.65, np.where(t < 3.0, 1.25, 0.8))
    left = envelope * (0.9 * np.sin(2 * np.pi * 220 * t) + 0.08 * np.sin(2 * np.pi * 950 * t))
    right = envelope * (
        0.8 * np.sin(2 * np.pi * 220 * t + 0.12) + 0.08 * np.sin(2 * np.pi * 950 * t)
    )
    audio = np.column_stack((left, right))

    def wait():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                not window.worker
                and not window.workspace.job
                and not window.workspace.timer.isActive()
            ):
                if errors:
                    raise RuntimeError(str(errors))
                return
            time.sleep(0.01)
        raise RuntimeError("Output demo timed out.")

    try:
        window.show()
        window.loaded(
            "source", (audio, rate, analyse(audio, rate), "Generated over-range float mix")
        )
        window.views.setStep("output")
        output = window.output_view
        output.gain.setValue(-6)
        output.render_button.click()
        wait()
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("output"))
        wait()
        window.grab().save(str(directory / "omazone-output-peaks.png"))
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        wait()
        window.grab().save(str(directory / "omazone-output-waveform.png"))
        print(
            f"Captured output screenshots in {directory}; prefixes: {window.renderer.computations}"
        )
    finally:
        if window.worker:
            window.worker.wait()
            app.processEvents()
        window.close()


if __name__ == "__main__":
    main()
