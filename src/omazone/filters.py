"""Offline-configured corrective IIR filters with shared stereo coefficients.

Butterworth high/low passes and a true-null notch use float64 second-order
sections. Each channel has independent history. There is no buffering delay,
but causal IIR phase and frequency-dependent group delay are not compensated.
"""

from dataclasses import dataclass

import numpy as np
from scipy import signal

from .engine import validate_audio

MAX_FILTERS = 6
SLOPES = (6, 12, 18, 24)


@dataclass(frozen=True)
class FilterSettings:
    kind: str = "highpass"
    frequency: float = 30.0
    slope: int = 12
    q: float = 1.0
    enabled: bool = True


@dataclass(frozen=True)
class FiltersSettings:
    filters: tuple[FilterSettings, ...] = ()


def _positive_integer(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")


def validate_settings(settings, rate):
    _positive_integer(rate, "Sample rate")
    if not isinstance(settings, FiltersSettings) or not isinstance(settings.filters, tuple):
        raise ValueError("Expected a tuple of corrective filter settings.")
    if len(settings.filters) > MAX_FILTERS:
        raise ValueError("At most six corrective filters are supported.")
    for item in settings.filters:
        if not isinstance(item, FilterSettings):
            raise ValueError("Invalid corrective filter settings.")
        if item.kind not in ("highpass", "lowpass", "notch"):
            raise ValueError("Filter kind must be highpass, lowpass or notch.")
        for value in (item.frequency, item.q):
            if (
                not isinstance(value, (int, float, np.number))
                or isinstance(value, (bool, np.bool_))
                or not np.isrealobj(value)
                or not np.isfinite(value)
            ):
                raise ValueError("Filter frequency and Q must be finite real numbers.")
        if not 20 <= item.frequency < rate / 2:
            raise ValueError("Filter frequency must be at least 20 Hz and below Nyquist.")
        if type(item.slope) is not int or item.slope not in SLOPES:
            raise ValueError("Filter slope must be 6, 12, 18 or 24 dB/octave.")
        if not 0.1 <= item.q <= 100:
            raise ValueError("Notch Q must be between 0.1 and 100.")
        # SciPy's notch bandwidth is f/Q. Reject widths reaching Nyquist rather
        # than silently designing a different or degenerate response.
        if item.kind == "notch" and item.frequency / item.q >= rate / 2:
            raise ValueError("Notch bandwidth must be below Nyquist; increase Q.")
        if type(item.enabled) is not bool:
            raise ValueError("Filter enabled state must be boolean.")


def design_sos(settings, rate):
    """Return the active cascade; an empty (0, 6) array means exact bypass.

    A slope of 6 dB/octave is order one, 12 is order two, and so on.
    SciPy pairs/scales Butterworth poles directly as SOS, avoiding high-order
    polynomial coefficients. Notch Q is centre frequency / -3 dB bandwidth.
    """
    validate_settings(settings, rate)
    sections = []
    for item in settings.filters:
        if not item.enabled:
            continue
        if item.kind == "notch":
            numerator, denominator = signal.iirnotch(item.frequency, item.q, fs=rate)
            sections.append(signal.tf2sos(numerator, denominator))
        else:
            sections.append(
                signal.butter(
                    item.slope // 6, item.frequency, btype=item.kind, fs=rate, output="sos"
                )
            )
    return np.vstack(sections) if sections else np.empty((0, 6), dtype=np.float64)


class FilterProcessor:
    """Block core with zero initial history and offline-only coefficient updates.

    prepare starts bypassed. set_settings replaces the cascade and resets all
    histories. reset does not alter coefficients. Never call either operation
    mid-playback as a substitute for a parameter-smoothing policy.
    """

    latency_samples = 0

    def prepare(self, sample_rate, channels, max_block_size):
        for value, name in (
            (sample_rate, "Sample rate"),
            (channels, "Channels"),
            (max_block_size, "Maximum block size"),
        ):
            _positive_integer(value, name)
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_block_size = max_block_size
        self.set_settings(FiltersSettings())

    def set_settings(self, settings):
        self.sos = design_sos(settings, self.sample_rate)
        self.reset()

    def reset(self):
        self.state = np.zeros((len(self.sos), 2, self.channels), dtype=np.float64)

    def process_block(self, block):
        block = np.asarray(block, dtype=np.float64)
        if (
            block.ndim != 2
            or block.shape[1] != self.channels
            or not 0 < len(block) <= self.max_block_size
            or not np.all(np.isfinite(block))
        ):
            raise ValueError("Block must be finite and match the prepared filter format and size.")
        if not len(self.sos):
            return block.copy()
        output, self.state = signal.sosfilt(self.sos, block, axis=0, zi=self.state)
        return output


def render_filters(audio, rate, settings, block_size=4096):
    """Render the whole recording, retaining its length and not appending IIR tails."""
    audio = validate_audio(audio)
    processor = FilterProcessor()
    processor.prepare(rate, audio.shape[1], block_size)
    processor.set_settings(settings)
    output = np.empty_like(audio)
    for start in range(0, len(audio), block_size):
        output[start : start + block_size] = processor.process_block(audio[start : start + block_size])
    return output


def frequency_response(settings, rate):
    """Whole-recording active cascade response, in Hz and dB, for a static plot.

    Plot values are floored at -240 dB. The uniform grid may miss a narrow
    notch's exact centre; use sosfreqz at the centre for depth verification.
    """
    sos = design_sos(settings, rate)
    if not len(sos):
        return np.linspace(0, rate / 2, 8192, endpoint=False), np.zeros(8192)
    frequency, response = signal.sosfreqz(sos, worN=8192, fs=rate)
    return frequency, 20 * np.log10(np.maximum(np.abs(response), 1e-12))
