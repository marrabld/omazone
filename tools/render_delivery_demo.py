"""Capture the Export delivery panel using generated, redistributable audio.

uv run python tools/render_delivery_demo.py docs/images
uv run python tools/render_delivery_demo.py docs/images --sample-rate 44100
"""

import argparse
import os
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
from PySide6 import QtWidgets

from omazone.engine import analyse
from omazone.gui import Window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--sample-rate", type=int, choices=(44100, 48000, 88200, 96000))
    arguments = parser.parse_args()
    directory = arguments.directory
    directory.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = Window()
    window.resize(1400, 1000)
    errors = []
    window.error = errors.append
    rate = 48000
    samples = np.arange(rate * 4) / rate
    noise = np.random.default_rng(9001).normal(0, 0.015, len(samples))
    left = (
        0.12 * np.sin(2 * np.pi * 55 * samples)
        + 0.3 * np.sin(2 * np.pi * 220 * samples)
        + 0.12 * np.sin(2 * np.pi * 1400 * samples)
        + noise
    )
    audio = np.column_stack((left, -0.7 * left))

    def wait():
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None
                and window.workspace.job is None
                and not window.workspace.timer.isActive()
                and window.export_view.review_job is None
                and not window.export_view.review_timer.isActive()
            ):
                if errors:
                    raise RuntimeError(str(errors))
                return
            time.sleep(0.01)
        raise RuntimeError("Delivery demo timed out.")

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated delivery demo"))
        window.compressor_view.enabled.setChecked(True)
        window.compressor_view.threshold.setValue(-26)
        window.compressor_view.ratio.setValue(2.5)
        window.output_view.enabled.setChecked(True)
        window.output_view.gain.setValue(3)
        window.output_view.limiter_enabled.setChecked(True)
        window.output_view.ceiling.setValue(-1)
        window.render_saved_recipe()
        wait()
        window.views.setStep("export")
        window.export_view.format.setCurrentIndex(window.export_view.format.findData("pcm_24"))
        window.export_view.draw(window.workflow_status())
        if arguments.sample_rate:
            window.export_view.sample_rate.setCurrentIndex(
                window.export_view.sample_rate.findData(arguments.sample_rate)
            )
        wait()
        window.tool_scroll.ensureWidgetVisible(window.export_view.delivery)
        for _ in range(5):
            app.processEvents()
        window.tool_scroll.ensureWidgetVisible(window.export_view.delivery_note, 0, 12)
        for _ in range(5):
            app.processEvents()
        path = directory / (
            "omazone-sample-rate.png" if arguments.sample_rate else "omazone-delivery.png"
        )
        if not window.grab().save(str(path)):
            raise RuntimeError("Could not save screenshot")
        print(f"Captured delivery screenshot in {directory}: {path}")
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.project.mark_saved()
        window.close()


if __name__ == "__main__":
    main()
