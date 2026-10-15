"""Bundled-app smoke test using generated audio, without an audio device."""

import numpy as np

from .dynamic_eq import DynamicEQSettings, dynamic_eq_parameters
from .engine import MatchSettings, analyse
from .multiband import MultibandResult
from .range_match import design_range_match
from .reference_variation import analyse_variation
from .sections import TargetProfile
from .waveform import SampleRegion


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
    stage = window.project.stages["dynamic-eq"]
    stage.bypassed = False
    stage.parameters = dynamic_eq_parameters(DynamicEQSettings(frequency=1200, threshold_db=-48))
    dynamic = window.get_renderer().render(window.project, block_size=257)
    if dynamic.dynamic_eq is None or dynamic.dynamic_eq.max_reduction_db <= 0:
        raise RuntimeError("Bundled dynamic EQ did not attenuate the target band.")
    if dynamic.output.shape != audio.shape or not np.all(np.isfinite(dynamic.output)):
        raise RuntimeError("Bundled dynamic EQ produced invalid audio.")
    reference = np.tile(audio, (20, 1))
    reference[rate * 2:] += (0.04 * np.sin(2 * np.pi * 3000 * np.arange(rate * 2) / rate))[:, None]
    variation = analyse_variation(reference, rate)
    if variation is None or not variation.available:
        raise RuntimeError("Bundled reference-variation analysis failed.")
    range_spec = design_range_match(reference, rate, variation, MatchSettings(method="range-aware-v1"))
    if np.max(np.abs(range_spec.requested_db)) > 1e-9:
        raise RuntimeError("Bundled range-aware matching changed an identical reference.")
    target = TargetProfile("selftest", "Generated reference", analyse(reference, rate), rate,
                           SampleRegion(0, len(reference)), "Generated", variation)
    window.project.reference_target = target
    window.views.setStep("match")
    if not window.reference_variation.draw(analyse(audio, rate), None, target):
        raise RuntimeError("Bundled reference-variation display failed.")
