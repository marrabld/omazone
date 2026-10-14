"""Bundled-app smoke test using generated audio, without an audio device."""

import numpy as np

from .engine import analyse
from .multiband import MultibandResult


def verify(window):
    rate = 16000
    time = np.arange(rate // 5) / rate
    left = 0.15 * np.sin(2 * np.pi * 80 * time) + 0.05 * np.sin(2 * np.pi * 1200 * time)
    audio = np.column_stack((left, -0.4 * left))
    window.loaded("source", (audio, rate, analyse(audio, rate), "Generated self-test audio"))
    window.views.setStep("dynamics")
    window.compressor_view.mode.setCurrentIndex(1)
    window.compressor_view.enabled.setChecked(True)
    window.output_view.limiter_enabled.setChecked(True)
    result = window.get_renderer().render(window.project, block_size=257)
    if not isinstance(result.compression, MultibandResult) or result.limiting is None:
        raise RuntimeError("Bundled three-band compression or limiter did not render.")
    if result.output.shape != audio.shape or not np.all(np.isfinite(result.output)):
        raise RuntimeError("Bundled render changed the format or produced non-finite samples.")
    if not np.allclose(result.output, audio, rtol=0, atol=1e-12):
        raise RuntimeError("Bundled neutral three-band reconstruction failed.")
    if len(result.compression.solo_audio) != 3:
        raise RuntimeError("Bundled render is missing band-solo buffers.")
