"""Capture the corrective-filter inspector using generated, redistributable audio.

uv run python tools/render_filter_demo.py docs/images
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
    window.resize(1400, 1100)
    errors = []
    window.error = errors.append
    rate = 48000
    time_axis = np.arange(rate * 5) / rate
    noise = np.random.default_rng(629).normal(0, 0.012, len(time_axis))
    left = (
        0.08 * np.sin(2 * np.pi * 12 * time_axis)
        + 0.06 * np.sin(2 * np.pi * 1000 * time_axis)
        + 0.15 * np.sin(2 * np.pi * 180 * time_axis)
        + 0.04 * np.sin(2 * np.pi * 6000 * time_axis) + noise
    )
    audio = np.column_stack((left, -0.6 * left))
    try:
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated filter demonstration"))
        window.show()
        window.manual_eq_view.filters_button.click()
        view = window.filter_view
        view.add_filter(35)
        view.slope.setCurrentIndex(view.slope.findData(24))
        view.add_filter(1000)
        view.kind.setCurrentIndex(view.kind.findData("notch"))
        view.q.setValue(12)
        view.add_filter(16000)
        view.kind.setCurrentIndex(view.kind.findData("lowpass"))
        view.slope.setCurrentIndex(view.slope.findData(12))
        view.filter_list.setCurrentRow(1)
        view.render_button.click()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            app.processEvents()
            if window.worker is None and window.workspace.job is None and not window.workspace.timer.isActive():
                break
            time.sleep(0.01)
        if window.worker is not None or window.workspace.job is not None or errors:
            raise RuntimeError(f"Filter demo render did not finish: {errors}")
        for _ in range(5):
            app.processEvents()
        path = directory / "omazone-filters.png"
        if not window.grab().save(str(path)):
            raise RuntimeError("Could not save screenshot")
        print(path)
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.project.mark_saved()
        window.close()


if __name__ == "__main__":
    main()
