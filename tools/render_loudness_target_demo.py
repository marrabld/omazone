"""Capture explicit targeting with generated, redistributable stereo audio.

uv run python tools/render_loudness_target_demo.py docs/images
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
    directory = parser.parse_args().directory
    directory.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = Window()
    window.resize(1500, 1300)
    errors = []
    window.error = errors.append
    rate = 48000
    audio = np.random.default_rng(102).normal(0, 0.07, (rate * 4, 2))
    audio[:: rate // 10] += [0.7, -0.4]

    def wait():
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None
                and window.output_view.targeting.job is None
                and window.export_view.review_job is None
                and not window.export_view.review_timer.isActive()
                and window.workspace.job is None
                and not window.workspace.timer.isActive()
                and window.stage_measurements.job is None
                and not window.stage_measurements.timer.isActive()
            ):
                if errors:
                    raise RuntimeError(str(errors))
                return
            time.sleep(0.005)
        raise RuntimeError("Loudness target demo timed out.")

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated transient stereo"))
        window.project.stages["match"].bypassed = True
        window.render_saved_recipe()
        wait()
        window.views.setStep("output")
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("spectrum"))
        window.export_view.sample_rate.setCurrentIndex(
            window.export_view.sample_rate.findData(44100)
        )
        window.output_view.target_toggle.setChecked(True)
        wait()
        targeting = window.output_view.targeting
        targeting.lufs.setValue(-14)
        targeting.ceiling.setValue(-1)
        targeting.mode.setCurrentIndex(targeting.mode.findData("limiter"))
        targeting.calculate()
        wait()
        if targeting.proposal is None or not targeting.proposal.converged:
            raise RuntimeError(targeting.message.text())

        def capture(name):
            for _ in range(8):
                app.processEvents()
            window.tool_scroll.ensureWidgetVisible(targeting, 0, 0)
            for _ in range(8):
                app.processEvents()
            path = directory / name
            if not window.grab().save(str(path)):
                raise RuntimeError("Could not save targeting screenshot")
            print(f"Captured {path}")

        capture("omazone-loudness-proposal.png")
        targeting.apply()
        wait()
        capture("omazone-loudness-target.png")
    finally:
        window.project.mark_saved()
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
