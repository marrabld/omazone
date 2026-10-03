"""Capture a deterministic loop/A-B demo without recording a desktop or audio.

Run with `uv run python tools/render_loop_demo.py /tmp/opencode/omazone-loop-frames`.
Frames use real widgets and the playback callback, with a simulated output device.
Encode them with FFmpeg as described in the README.
"""

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
import sounddevice as sd
from PySide6 import QtWidgets
from scipy import signal

from omazone.engine import analyse, audition_pair, design_match, render
from omazone.gui import Window
from omazone.waveform import PeakIndex, SampleRegion


class DemoStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    sd.OutputStream = DemoStream
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = Window()
    window.resize(1120, 880)
    rate = 48000
    time = np.arange(rate * 6) / rate
    envelope = 0.15 + 0.65 * np.exp(-6 * (time % 0.5))
    audio = np.column_stack(
        (
            envelope * (0.5 * np.sin(2 * np.pi * 83 * time) + 0.2 * np.sin(2 * np.pi * 249 * time)),
            envelope
            * (0.45 * np.sin(2 * np.pi * 83 * time + 0.15) + 0.2 * np.sin(2 * np.pi * 332 * time)),
        )
    )
    reference = signal.sosfilt(signal.butter(2, 180, fs=rate, output="sos"), audio, axis=0)
    source_spectrum, target_spectrum = analyse(audio, rate), analyse(reference, rate)
    window.loaded(
        "source", (audio, rate, source_spectrum, "Generated stereo demo", PeakIndex(audio))
    )
    window.loaded(
        "reference", (reference, rate, target_spectrum, "Generated reference", PeakIndex(reference))
    )
    spec = design_match(source_spectrum, target_spectrum, rate)
    processed = render(audio, spec)
    window.processed((processed, analyse(processed, rate), spec, audition_pair(audio, processed)))
    window.update_buttons()
    window.waveform.set_selection(SampleRegion(rate, int(rate * 2.5)))
    window.waveform.channel_plots[0].setXRange(0.5, 3.0, padding=0)
    window.loop_selection.setChecked(True)
    window.play_selection()
    window.show()
    app.processEvents()
    window.waveform.redraw()
    block = np.empty((rate // 12, 2), dtype=np.float32)
    for frame in range(60):
        if frame == 30:
            window.ab_button.click()
        if frame:
            window.stream.callback(block, len(block), None, None)
        window.check_playback()
        version = "processed" if window.listen_processed else "original"
        window.status.setText(f"Loop demo: {version} | 1.0-2.5 s | same cursor for A/B")
        app.processEvents()
        if not window.grab().save(str(directory / f"frame-{frame:03d}.png")):
            raise RuntimeError("Could not save demo frame.")
    window.close()
    print(f"Captured 60 frames at 12 fps in {directory}")


if __name__ == "__main__":
    main()
