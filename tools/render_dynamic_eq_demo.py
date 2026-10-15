"""Capture one-band dynamic EQ with generated audio and no audio device.

uv run python tools/render_dynamic_eq_demo.py docs/images
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
    rate = 48000
    t = np.arange(rate * 4) / rate
    bursts = np.where(((t > 0.6) & (t < 1.2)) | ((t > 2) & (t < 2.8)), 0.45, 0.012)
    left = bursts * np.sin(2 * np.pi * 3000 * t) + 0.05 * np.sin(2 * np.pi * 200 * t)
    audio = np.column_stack((left, left * 0.7))
    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated dynamic EQ bursts"))
        window.views.setStep("dynamic-eq")
        view = window.dynamic_eq_view
        for field, value in (
            ("frequency", 3000),
            ("q", 3),
            ("threshold_db", -26),
            ("max_cut_db", 9),
        ):
            view.controls[field].setValue(value)
        window.render_saved_recipe()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            app.processEvents()
            meter = window.stage_measurements
            if window.worker is None and meter.job is None and not meter.timer.isActive():
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("Dynamic EQ demo timed out")
        if errors:
            raise RuntimeError(str(errors))
        window.tool_scroll.verticalScrollBar().setValue(0)
        for _ in range(8):
            app.processEvents()
        path = directory / "omazone-dynamic-eq.png"
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
