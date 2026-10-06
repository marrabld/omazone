"""One causal bell EQ, with optional region-limited gain and stereo-linked paths.

The IIR runs with continuous history across the recording. Within a named region
its wet output fades in/out with complementary amplitude weights. Outside the
region samples are copied exactly, and the file retains its original length.
"""

from dataclasses import dataclass

import numpy as np
from scipy import signal

from .engine import validate_audio


@dataclass(frozen=True)
class BellSettings:
    frequency: float = 2400.0
    gain_db: float = 0.0
    q: float = 1.0
    region_id: str | None = None
    transition_ms: float = 75.0


def settings_from_parameters(parameters):
    if not parameters:
        return BellSettings()
    if parameters.get("kind", "bell-v1") != "bell-v1" or "bands" in parameters:
        raise ValueError("This version supports one bell band. Saved EQ settings are retained.")
    if set(parameters) - {"kind", "band"} or not isinstance(parameters.get("band"), dict):
        raise ValueError("Unrecognised saved EQ settings. They have not been replaced.")
    try:
        return BellSettings(**parameters["band"])
    except TypeError as error:
        raise ValueError("Invalid manual EQ band settings.") from error


def validate_settings(settings, rate, regions=None):
    if not all(
        np.isfinite(value)
        for value in (settings.frequency, settings.gain_db, settings.q, settings.transition_ms)
    ):
        raise ValueError("EQ settings must be finite.")
    if not 20 <= settings.frequency < rate / 2:
        raise ValueError("EQ frequency must be at least 20 Hz and below Nyquist.")
    if not -18 <= settings.gain_db <= 18 or not 0.1 <= settings.q <= 20:
        raise ValueError("EQ supports +/-18 dB gain and Q from 0.1 to 20.")
    if not 0 <= settings.transition_ms <= 5000:
        raise ValueError("EQ transition must be between zero and 5000 ms.")
    if (
        regions is not None
        and settings.region_id is not None
        and settings.region_id not in {item.id for item in regions}
    ):
        raise ValueError("The EQ region is missing. Choose an existing region or Whole recording.")


def bell_sos(settings, rate):
    validate_settings(settings, rate)
    a = 10 ** (settings.gain_db / 40)
    omega = 2 * np.pi * settings.frequency / rate
    alpha = np.sin(omega) / (2 * settings.q)
    numerator = np.asarray([1 + alpha * a, -2 * np.cos(omega), 1 - alpha * a])
    denominator = np.asarray([1 + alpha / a, -2 * np.cos(omega), 1 - alpha / a])
    return np.concatenate((numerator / denominator[0], denominator / denominator[0]))[None, :]


class BellProcessor:
    """Readable block processor; no lookahead, no fixed latency, infinite IIR tail.

    The offline renderer does not append the infinite tail. Coefficient changes
    reset state and are offline operations; smooth region gain is applied afterward.
    """

    latency_samples = 0

    def prepare(self, sample_rate, channels, max_block_size):
        if min(sample_rate, channels, max_block_size) <= 0:
            raise ValueError("Preparation values must be positive.")
        self.sample_rate, self.channels, self.max_block_size = sample_rate, channels, max_block_size
        self.set_settings(BellSettings(frequency=min(2400, sample_rate / 4)))

    def set_settings(self, settings):
        self.sos = bell_sos(settings, self.sample_rate)
        self.reset()

    def reset(self):
        self.state = np.zeros((1, 2, self.channels))

    def process_block(self, block):
        block = np.asarray(block, dtype=float)
        if (
            block.ndim != 2
            or block.shape[1] != self.channels
            or not 0 < len(block) <= self.max_block_size
        ):
            raise ValueError("Block differs from the prepared EQ format or size.")
        output, self.state = signal.sosfilt(self.sos, block, axis=0, zi=self.state)
        return output


def region_envelope(length, rate, settings, regions):
    """Wet weight; transitions lie inside the half-open region, never outside it."""
    if settings.region_id is None:
        return None
    region = next(item.bounds for item in regions if item.id == settings.region_id)
    if not 0 <= region.start < region.end <= length:
        raise ValueError("EQ region is outside the recording.")
    weights = np.ones(region.end - region.start)
    fade = min(round(settings.transition_ms * rate / 1000), len(weights) // 2)
    if fade:
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, fade))
        if region.start > 0:
            weights[:fade] = ramp
        if region.end < length:
            weights[-fade:] = ramp[::-1]
    return region, weights


def render_eq(audio, rate, settings, regions=(), block_size=4096):
    audio = validate_audio(audio)
    validate_settings(settings, rate, regions)
    if type(block_size) is not int or block_size <= 0:
        raise ValueError("EQ block size must be a positive integer.")
    if settings.gain_db == 0:
        return audio.copy()
    processor = BellProcessor()
    processor.prepare(rate, audio.shape[1], block_size)
    processor.set_settings(settings)
    wet = np.empty_like(audio)
    for start in range(0, len(audio), block_size):
        block = audio[start : start + block_size]
        wet[start : start + len(block)] = processor.process_block(block)
    assignment = region_envelope(len(audio), rate, settings, regions)
    if assignment is None:
        return wet
    bounds, weights = assignment
    output = audio.copy()
    output[bounds.start : bounds.end] += (
        wet[bounds.start : bounds.end] - audio[bounds.start : bounds.end]
    ) * weights[:, None]
    return output


def frequency_response(settings, rate):
    frequency, response = signal.sosfreqz(bell_sos(settings, rate), worN=8192, fs=rate)
    return frequency, 20 * np.log10(np.maximum(np.abs(response), 1e-12))
