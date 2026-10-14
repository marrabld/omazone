"""Capture three-band controls and reduction history from generated signals.

uv run python tools/render_multiband_demo.py docs/images
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
    window.resize(1400, 1000)
    errors = []
    window.error = errors.append
    rate = 16000
    samples = np.arange(rate * 8) / rate
    envelope = np.where(samples < 2, 0.45, np.where(samples < 6, 1.0, 0.65))
    left = envelope * (
        0.6 * np.sin(2 * np.pi * 60 * samples)
        + 0.16 * np.sin(2 * np.pi * 900 * samples)
        + 0.08 * np.sin(2 * np.pi * 5000 * samples)
    )
    audio = np.column_stack((left, -0.4 * left))

    def wait():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None and window.workspace.job is None
                and not window.workspace.timer.isActive()
            ):
                if errors:
                    raise RuntimeError(str(errors))
                return
            time.sleep(0.01)
        raise RuntimeError("Multiband demo timed out.")

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated bass, mids and treble"))
        window.views.setStep("dynamics")
        comp = window.compressor_view
        comp.mode.setCurrentIndex(1)
        comp.enabled.setChecked(True)
        comp.threshold.setValue(-28)
        comp.ratio.setValue(3)
        comp.band_selector.setCurrentIndex(1)
        comp.threshold.setValue(-24)
        comp.ratio.setValue(2)
        comp.render_button.click()
        wait()
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("output"))
        wait()
        window.grab().save(str(directory / "omazone-multiband.png"))
        print(f"Captured multiband screenshot in {directory}; prefixes: {window.renderer.computations}")
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.project.mark_saved()
        window.close()


if __name__ == "__main__":
    main()
