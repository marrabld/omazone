"""Offline stereo-linked lookahead limiting with an attenuation-only peak guard.

The block core delays audio, holds required gain for one lookahead window and
smooths it over another. The offline wrapper compensates that delay and checks
8x reconstructed peaks. A residual inter-sample overshoot triggers an explicit
whole-file attenuation, recorded separately and included in the reduction trace.
There is no makeup gain, loudness target or clipping of individual samples.
"""

from dataclasses import asdict, dataclass

import numpy as np
from scipy import ndimage, signal

from .engine import validate_audio

GUARD_OVERSAMPLE = 8
GUARD_MARGIN_DB = 0.05


@dataclass(frozen=True)
class LimiterSettings:
    ceiling_db: float = -1.0
    lookahead_ms: float = 5.0
    release_ms: float = 100.0


def settings_from_parameters(parameters):
    if not parameters:
        return LimiterSettings()
    if not isinstance(parameters, dict) or parameters.get("kind") != "limiter-v1":
        raise ValueError("Unsupported limiter recipe; saved settings are retained.")
    if set(parameters) != {"kind", *asdict(LimiterSettings())}:
        raise ValueError("Invalid saved limiter settings.")
    return LimiterSettings(**{key: value for key, value in parameters.items() if key != "kind"})


def limiter_parameters(settings):
    return {"kind": "limiter-v1", **asdict(settings)}


def validate_settings(settings):
    values = (settings.ceiling_db, settings.lookahead_ms, settings.release_ms)
    if any(
        not isinstance(value, (int, float, np.number))
        or isinstance(value, (bool, np.bool_))
        or not np.isfinite(value)
        for value in values
    ):
        raise ValueError("Limiter settings must be finite numbers.")
    if not -24 <= settings.ceiling_db <= 0:
        raise ValueError("Limiter ceiling must be -24 to 0 dB.")
    if not 1 <= settings.lookahead_ms <= 20 or not 10 <= settings.release_ms <= 2000:
        raise ValueError("Limiter lookahead must be 1 to 20 ms and release 10 to 2000 ms.")


class LimiterProcessor:
    """Stateful native-rate peak limiter; true-peak checking is an offline pass."""

    def prepare(self, sample_rate, channels, max_block_size):
        if (
            any(type(value) is not int for value in (sample_rate, channels, max_block_size))
            or min(sample_rate, channels, max_block_size) <= 0
            or channels not in (1, 2)
        ):
            raise ValueError("Invalid limiter preparation format.")
        self.rate, self.channels, self.max_block_size = sample_rate, channels, max_block_size
        self.set_settings(LimiterSettings())

    def set_settings(self, settings):
        validate_settings(settings)
        self.settings = settings
        self.ceiling = 10 ** (settings.ceiling_db / 20)
        self.latency_samples = max(1, round(self.rate * settings.lookahead_ms / 1000))
        self.release_exponent = 1000 / (self.rate * settings.release_ms)
        # Exponential rescaling makes the release recurrence a vectorised
        # maximum scan. Bound its exponent to avoid overflow on low-rate tests.
        self.release_chunk = min(self.max_block_size, max(1, int(20 / self.release_exponent)))
        self.decay = np.exp(-self.release_exponent * np.arange(1, self.release_chunk + 1))
        self.reset()

    def reset(self):
        delay = self.latency_samples
        self.audio_history = np.zeros((delay, self.channels))
        self.peak_history = np.zeros(delay)
        self.gain_history = np.ones(delay)
        self.reduction = 0.0
        self.last_reduction_db = np.empty(0)

    def process_block(self, block):
        block = np.asarray(block, dtype=float)
        if (
            block.ndim != 2
            or block.shape[1] != self.channels
            or not 0 < len(block) <= self.max_block_size
            or not np.all(np.isfinite(block))
        ):
            raise ValueError("Block differs from the prepared limiter format or size.")
        delay = self.latency_samples
        peaks = np.r_[self.peak_history, np.max(np.abs(block), axis=1)]
        held = ndimage.maximum_filter1d(
            peaks, size=delay + 1, origin=delay // 2, mode="constant", cval=0
        )[delay:]
        self.peak_history = peaks[-delay:].copy()
        target = np.minimum(1.0, self.ceiling / np.maximum(held, 1e-30))
        reductions = 1 - target
        released = np.empty(len(block))
        for start in range(0, len(block), self.release_chunk):
            count = min(self.release_chunk, len(block) - start)
            decay = self.decay[:count]
            envelope = (
                np.maximum.accumulate(
                    np.maximum(reductions[start : start + count] / decay, self.reduction)
                )
                * decay
            )
            self.reduction = float(envelope[-1])
            released[start : start + count] = 1 - envelope
        gains = np.r_[self.gain_history, released]
        summed = np.r_[0.0, np.cumsum(gains)]
        smoothed = (summed[delay + 1 :] - summed[: -(delay + 1)]) / (delay + 1)
        self.gain_history = gains[-delay:].copy()
        delayed = np.concatenate((self.audio_history, block))
        self.audio_history = delayed[len(block) :].copy()
        audio = delayed[: len(block)]
        # Protect the sample ceiling from summation roundoff at a strong peak.
        gain = np.minimum(
            np.clip(smoothed, 0, 1),
            self.ceiling / np.maximum(np.max(np.abs(audio), axis=1), 1e-30),
        )
        self.last_reduction_db = 20 * np.log10(np.maximum(gain, 1e-30))
        return audio * gain[:, None]


def reconstructed_peak(audio, block_size=65536, cancelled=None):
    """Conservative 8x peak, with zero-padded interior and reflected edge checks.

    Unlike the display meter this protection pass cannot omit short files or
    edge samples. Chunk overlap is context only; its padded edges are not read.
    """
    audio = validate_audio(audio)
    if type(block_size) is not int or block_size <= 0:
        raise ValueError("Peak-check block size must be a positive integer.")
    peak = float(np.max(np.abs(audio)))
    if peak == 0:
        return 0.0
    overlap = 32
    for start in range(0, len(audio), block_size):
        if cancelled is not None and cancelled():
            raise RuntimeError("Limiter rendering cancelled.")
        stop = min(len(audio), start + block_size)
        low, high = max(0, start - overlap), min(len(audio), stop + overlap)
        upsampled = signal.resample_poly(
            audio[low:high], GUARD_OVERSAMPLE, 1, axis=0, window=("kaiser", 14.0)
        )
        interior = upsampled[(start - low) * GUARD_OVERSAMPLE : (stop - low) * GUARD_OVERSAMPLE]
        peak = max(peak, float(interior.max()), -float(interior.min()))
    # Decoder resamplers can reflect file edges rather than pad with silence.
    # A longer interpolator catches their higher ringing without changing the
    # interior meter or allocating an oversampled full-length recording.
    edge_filter = signal.firwin(
        64 * GUARD_OVERSAMPLE + 1, 1 / GUARD_OVERSAMPLE, window=("kaiser", 8.6)
    )
    count = min(64, len(audio)) * GUARD_OVERSAMPLE
    for edge, head in ((audio[:128], True), (audio[-128:], False)):
        reconstructed = signal.resample_poly(
            edge, GUARD_OVERSAMPLE, 1, axis=0, window=edge_filter, padtype="reflect"
        )
        boundary = reconstructed[:count] if head else reconstructed[-count:]
        peak = max(peak, float(boundary.max()), -float(boundary.min()))
    return peak


@dataclass(frozen=True)
class LimitingResult:
    audio: np.ndarray
    time: np.ndarray
    reduction_db: np.ndarray
    max_reduction_db: float
    latency_samples: int
    guard_reduction_db: float
    reconstructed_peak_db: float | None


def render_limiter(audio, rate, settings, block_size=4096, cancelled=None):
    audio = validate_audio(audio)
    validate_settings(settings)
    if type(block_size) is not int or block_size <= 0:
        raise ValueError("Limiter block size must be a positive integer.")
    processor = LimiterProcessor()
    processor.prepare(rate, audio.shape[1], block_size)
    processor.set_settings(settings)
    delay = processor.latency_samples
    stride = max(1, round(rate / 100))
    indices = np.arange(0, len(audio), stride) + delay
    reduction = np.empty(len(indices))
    output = np.empty_like(audio)
    maximum = 0.0

    def process(block, start):
        nonlocal maximum
        rendered = processor.process_block(block)
        low, high = max(start, delay), min(start + len(block), len(audio) + delay)
        if low < high:
            output[low - delay : high - delay] = rendered[low - start : high - start]
            maximum = max(
                maximum, -float(np.min(processor.last_reduction_db[low - start : high - start]))
            )
        first, last = np.searchsorted(indices, (start, start + len(block)))
        reduction[first:last] = processor.last_reduction_db[indices[first:last] - start]

    for start in range(0, len(audio), block_size):
        if cancelled is not None and cancelled():
            raise RuntimeError("Limiter rendering cancelled.")
        process(audio[start : start + block_size], start)
    for tail in range(0, delay, block_size):
        process(np.zeros((min(block_size, delay - tail), audio.shape[1])), len(audio) + tail)

    peak = reconstructed_peak(output, cancelled=cancelled)
    ceiling = 10 ** (settings.ceiling_db / 20)
    guard = min(1.0, ceiling * 10 ** (-GUARD_MARGIN_DB / 20) / peak) if peak else 1.0
    guard_db = 20 * np.log10(guard)
    if guard < 1:
        output *= guard
        reduction += guard_db
    return LimitingResult(
        output,
        (indices - delay) / rate,
        reduction,
        maximum - guard_db,
        delay,
        -guard_db,
        20 * np.log10(peak * guard) if peak else None,
    )
