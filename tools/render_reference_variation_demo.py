"""Capture measured reference density with generated, redistributable signals.

uv run python tools/render_reference_variation_demo.py docs/images
uv run python tools/render_reference_variation_demo.py docs/images --range-aware
"""

import argparse
import os
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
from PySide6 import QtWidgets
from scipy import signal

from omazone.engine import analyse
from omazone.gui import Window
from omazone.reference_variation import analyse_variation
from omazone.waveform import PeakIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--range-aware", action="store_true")
    arguments = parser.parse_args()
    directory = arguments.directory
    directory.mkdir(parents=True, exist_ok=True)
    rate = 48000
    t = np.arange(rate * 12) / rate
    noise = np.random.default_rng(9801).normal(0, 1, len(t))
    coloured = signal.lfilter([0.04], [1, -0.98], noise)
    brightness = 0.15 + 0.85 * np.sin(2 * np.pi * 0.21 * t) ** 2
    left = (
        0.09 * np.sin(2 * np.pi * 110 * t)
        + 0.07 * np.sin(2 * np.pi * 440 * t)
        + coloured
        + 0.04 * brightness * noise
    )
    reference = np.column_stack((left, left * 0.8))
    mix = signal.sosfilt(signal.butter(1, 3000, fs=rate, output="sos"), reference, axis=0)
    # Generate statistics before opening a window; normal app loading uses a worker.
    variation = analyse_variation(reference, rate)
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = Window()
    window.resize(1400, 1000)
    errors = []
    window.error = errors.append
    try:
        window.show()
        window.loaded("source", (mix, rate, analyse(mix, rate), "Generated mix"))
        window.loaded(
            "reference",
            (
                reference,
                rate,
                analyse(reference, rate),
                "Generated reference",
                PeakIndex(reference),
                None,
                variation,
            ),
        )
        window.views.setStep("match")
        if arguments.range_aware:
            window.match_method.setCurrentIndex(window.match_method.findData("range-aware-v1"))
        window.process()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None
                and window.workspace.job is None
                and not window.workspace.timer.isActive()
                and window.stage_measurements.job is None
                and not window.stage_measurements.timer.isActive()
            ):
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("Reference variation demo timed out")
        if errors:
            raise RuntimeError(str(errors))
        window.tool_scroll.ensureWidgetVisible(window.reference_variation)
        for _ in range(8):
            app.processEvents()
        path = directory / (
            "omazone-range-aware.png"
            if arguments.range_aware
            else "omazone-reference-variation.png"
        )
        if not window.grab().save(str(path)):
            raise RuntimeError("Could not save screenshot")
        print(f"Captured {path}")
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.project.mark_saved()
        window.close()


if __name__ == "__main__":
    main()
