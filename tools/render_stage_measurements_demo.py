"""Capture stage measurements using generated audio, without an audio device.

uv run python tools/render_stage_measurements_demo.py docs/images
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
    window.resize(1280, 900)
    errors = []
    window.error = errors.append
    rate = 48000
    t = np.arange(rate * 4) / rate
    envelope = 0.15 + 0.3 * np.sin(2 * np.pi * 0.7 * t) ** 2
    left = envelope * (np.sin(2 * np.pi * 220 * t) + 0.3 * np.sin(2 * np.pi * 1100 * t))
    audio = np.column_stack((left, left * 0.8))

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated dynamics demo"))
        window.views.setStep("dynamics")
        window.compressor_view.enabled.setChecked(True)
        window.compressor_view.threshold.setValue(-24)
        window.compressor_view.ratio.setValue(3)
        window.render_saved_recipe()
        window.waveform.set_selection(SampleRegion(rate, rate * 3))
        meter = window.stage_measurements
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None
                and meter.job is None
                and not meter.timer.isActive()
                and window.workspace.job is None
                and not window.workspace.timer.isActive()
            ):
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("Stage measurement demo timed out")
        if errors:
            raise RuntimeError(str(errors))
        window.tool_scroll.ensureWidgetVisible(meter)
        for _ in range(8):
            app.processEvents()
        path = directory / "omazone-stage-measurements.png"
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
