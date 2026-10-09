"""Capture repair controls and reconstructed peaks from generated stereo audio.

Run: uv run python tools/render_declipping_demo.py docs/images
No audio device or private recording is used.
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
from omazone.waveform import SampleRegion


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = Window()
    window.resize(1120, 850)
    errors = []
    window.error = errors.append
    rate = 48000
    t = np.arange(rate // 5) / rate
    clean = 0.9 * np.sin(2 * np.pi * 440 * t)
    left = np.clip(clean, -0.45, 0.6)
    right = 0.32 * np.sin(2 * np.pi * 660 * t + 0.3)
    right[3120:3128] = 1.15  # Over-range-only rows remain ineligible.
    audio = np.column_stack((left, right))

    def wait():
        deadline = time.monotonic() + 20
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        if window.worker is not None or errors:
            raise RuntimeError(f"Demo operation failed: {errors}")

    try:
        window.loaded(
            "source", (audio, rate, analyse(audio, rate), "Generated short-gap repair demo")
        )
        window.waveform.set_selection(SampleRegion(2400, 4320))
        inspector = window.clipping_inspector
        window.views.setStep("repair")
        window.show()
        app.processEvents()
        window.grab().save(str(directory / "omazone-clipping-guided.png"))
        inspector.find_peaks()
        wait()
        inspector.review_button.click()
        app.processEvents()
        window.grab().save(str(directory / "omazone-clipping-review.png"))
        window.waveform.channel_plots[0].setXRange(0.061, 0.072, padding=0)
        window.seek(3120)
        window.show_waveform(signal="original")
        app.processEvents()
        window.waveform.redraw()
        app.processEvents()
        window.grab().save(str(directory / "omazone-clipping-markers.png"))
        window.views.setStep("repair")
        inspector.check_shown(True)
        inspector.repair_button.click()
        wait()
        if window.repair_result is None:
            raise RuntimeError("Demo produced no reconstructed intervals.")
        window.show()
        window.views.setStep("repair")
        app.processEvents()
        window.grab().save(str(directory / "omazone-declipping-controls.png"))
        window.waveform.channel_plots[0].setXRange(0.061, 0.072, padding=0)
        window.seek(3120)
        window.show_waveform(signal="original")
        app.processEvents()
        window.waveform.redraw()
        app.processEvents()
        window.grab().save(str(directory / "omazone-declipping-waveform.png"))
        window.views.setStep("repair")
        inspector.advanced_toggle.setChecked(True)
        window.resize(1360, 1300)
        app.processEvents()
        window.grab().save(str(directory / "omazone-clipping-inspection.png"))
        mask = np.zeros(len(audio), dtype=bool)
        for interval in window.repair_result.repaired:
            mask[interval.start : interval.end] = True
        before = np.mean((left[mask] - clean[mask]) ** 2)
        after = np.mean((window.repair_result.audio[mask, 0] - clean[mask]) ** 2)
        print(
            f"Captured repair screenshots in {directory}; local error ratio {after / before:.4f}."
        )
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


if __name__ == "__main__":
    main()
