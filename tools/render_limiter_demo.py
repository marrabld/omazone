"""Capture the final limiter inspector and reduction history from generated audio.

uv run python tools/render_limiter_demo.py docs/images
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
    time_axis = np.arange(rate * 8) / rate
    envelope = np.where(time_axis < 2, 0.4, np.where(time_axis < 6, 1.4, 0.6))
    left = envelope * (
        np.sin(2 * np.pi * 60 * time_axis)
        + 0.15 * np.sin(2 * np.pi * 4000 * time_axis + np.pi / 4)
    )
    audio = np.column_stack((left, -0.6 * left))
    audio[rate * 3, 0] = 2.5

    def wait():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None
                and window.workspace.job is None
                and not window.workspace.timer.isActive()
            ):
                if errors:
                    raise RuntimeError(str(errors))
                return
            time.sleep(0.01)
        raise RuntimeError("Limiter demo timed out.")

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated bass and transients"))
        window.views.setStep("output")
        window.output_view.gain.setValue(1)
        window.output_view.limiter_enabled.setChecked(True)
        window.output_view.render_button.click()
        wait()
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("output"))
        wait()
        window.grab().save(str(directory / "omazone-limiter.png"))
        print(f"Captured limiter screenshot in {directory}; prefixes: {window.renderer.computations}")
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.project.mark_saved()
        window.close()


if __name__ == "__main__":
    main()
