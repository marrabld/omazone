"""Capture the compression inspector and traces with generated audio, without a device.

uv run python tools/render_compressor_demo.py docs/images
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
    envelope = np.where(t < 1, 0.12, np.where(t < 3, 0.7, 0.18))
    envelope *= 0.75 + 0.25 * np.cos(2 * np.pi * 3 * t)
    audio = np.column_stack(
        (
            envelope * (0.9 * np.sin(2 * np.pi * 240 * t) + 0.15 * np.sin(2 * np.pi * 900 * t)),
            envelope
            * (0.7 * np.sin(2 * np.pi * 240 * t + 0.2) + 0.18 * np.sin(2 * np.pi * 900 * t)),
        )
    )

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
        raise RuntimeError("Compressor demo timed out.")

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated dynamic mix"))
        window.manual_eq_view.add_band(950, -2)
        window.views.setStep("dynamics")
        compressor = window.compressor_view
        compressor.threshold.setValue(-22)
        compressor.ratio.setValue(3)
        compressor.enabled.setChecked(True)
        compressor.render_button.click()
        wait()
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("output"))
        wait()
        window.grab().save(str(directory / "omazone-compression.png"))
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        wait()
        window.grab().save(str(directory / "omazone-compression-waveform.png"))
        print(
            f"Captured compressor screenshots in {directory}; prefixes: {window.renderer.computations}"
        )
    finally:
        if window.worker:
            window.worker.wait()
            app.processEvents()
        # The demos never save, so skip the prompt that would block a headless run.
        window.project.mark_saved()
        window.close()


if __name__ == "__main__":
    main()
