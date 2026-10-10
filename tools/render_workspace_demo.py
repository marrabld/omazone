"""Capture the persistent viewer using generated audio, no display/device needed.

uv run python tools/render_workspace_demo.py docs/images
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
from omazone.sections import capture_target
from omazone.waveform import SampleRegion


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = Window()
    window.resize(1280, 1100)
    errors = []
    window.error = errors.append
    rate = 16000
    t = np.arange(rate * 4) / rate
    envelope = 0.25 + 0.25 * np.cos(2 * np.pi * t / 2) ** 2
    clean = envelope * np.sin(2 * np.pi * 330 * t)
    audio = np.column_stack((np.clip(clean, -0.3, 0.35), clean * 0.6))
    reference = np.column_stack((clean, clean * 0.9))

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
                return
            time.sleep(0.01)
        raise RuntimeError("Demo jobs timed out")

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "Generated stereo passage"))
        window.loaded(
            "reference", (reference, rate, analyse(reference, rate), "Generated reference")
        )
        window.waveform.set_selection(SampleRegion(rate, rate * 3))
        window.waveform.channel_plots[0].setXRange(0.8, 3.2, padding=0)
        target = capture_target(
            reference,
            rate,
            SampleRegion(0, rate * 2),
            "Clean target",
            "clean",
            "Generated reference",
        )
        workbench = window.section_workbench
        workbench.target_captured(target)
        workbench.use_mix_selection()
        workbench.name.setText("Acoustic passage")
        workbench.add_section()
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        window.views.setStep("sections")
        wait()
        window.workspace_split.setSizes([580, 320])
        app.processEvents()
        window.grab().save(str(directory / "omazone-shared-sections.png"))
        window.views.setStep("listen")
        window.region_name.setText("Acoustic passage")
        app.processEvents()
        window.grab().save(str(directory / "omazone-waveform-selection.png"))
        window.grab().save(str(directory / "omazone-shared-regions.png"))
        window.views.setStep("reference")
        workbench.target_list.clearSelection()
        workbench.target_list.setCurrentItem(None)
        window.workspace.refresh_reference()
        workbench.reference_waveform.select_seconds(0.2, 1.7)
        window.workspace.mode.setCurrentIndex(0)
        wait()
        window.grab().save(str(directory / "omazone-shared-reference.png"))
        window.views.setStep("repair")
        window.clipping_inspector.find_peaks()
        wait()
        window.workspace.mode.setCurrentIndex(0)
        app.processEvents()
        window.grab().save(str(directory / "omazone-shared-clipping.png"))
        print(f"Captured shared viewer screenshots in {directory}")
    finally:
        if window.worker:
            window.worker.wait()
            app.processEvents()
        # The demos never save, so skip the prompt that would block a headless run.
        window.project.mark_saved()
        window.close()


if __name__ == "__main__":
    main()
