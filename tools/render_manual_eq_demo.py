"""Capture the manual EQ milestone using generated audio, without a device.

uv run python tools/render_manual_eq_demo.py docs/images
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
from omazone.project import NamedRegion
from omazone.waveform import SampleRegion


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
    envelope = 0.25 + 0.6 * np.exp(-5 * (t % 0.5))
    noise = np.random.default_rng(140).normal(0, 0.035, (len(t), 2))
    audio = (
        np.column_stack(
            (
                envelope
                * (0.2 * np.sin(2 * np.pi * 220 * t) + 0.13 * np.sin(2 * np.pi * 2200 * t)),
                envelope
                * (0.18 * np.sin(2 * np.pi * 220 * t + 0.15) + 0.11 * np.sin(2 * np.pi * 2200 * t)),
            )
        )
        + noise
    )
    reference = signal.sosfilt(signal.butter(2, 3500, fs=rate, output="sos"), audio, axis=0)

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
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("EQ demo timed out.")
        for _ in range(10):
            app.processEvents()
            time.sleep(0.01)

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated guitar-like mix"))
        window.loaded(
            "reference", (reference, rate, analyse(reference, rate), "Generated reference")
        )
        window.process()
        wait()
        window.project.regions.append(
            NamedRegion("guitar", "Acoustic passage", SampleRegion(rate, rate * 3))
        )
        window.refresh_named_regions()
        window.views.setStep("eq")
        eq = window.manual_eq_view
        eq.region.setCurrentIndex(eq.region.findData("guitar"))
        eq.canvas.placed.emit(2200, -3)
        eq.canvas.placed.emit(320, 2)
        eq.render_button.click()
        wait()
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("output"))
        wait()
        window.grab().save(str(directory / "omazone-manual-eq.png"))
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        window.waveform.channel_plots[0].setXRange(0.8, 3.2, padding=0)
        wait()
        window.waveform.redraw()
        app.processEvents()
        window.grab().save(str(directory / "omazone-manual-eq-region.png"))
        print(
            f"Captured manual EQ screenshots in {directory}; prefix computations: {window.renderer.computations}"
        )
    finally:
        if window.worker:
            window.worker.wait()
            app.processEvents()
        window.close()


if __name__ == "__main__":
    main()
