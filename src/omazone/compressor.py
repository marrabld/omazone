"""Offline stereo-linked broadband compression with a causal detector.

The same envelope controls both channels. There is no lookahead, fixed latency,
automatic makeup gain or oversampled peak protection. Diagnostics are sampled
from the actual detector and gain envelope, not reconstructed from output audio.
"""

from dataclasses import asdict, dataclass
from math import exp, log10, sqrt

import numpy as np

from .engine import validate_audio


@dataclass(frozen=True)
class CompressorSettings:
    threshold_db: float = -18.0
    ratio: float = 2.0
    attack_ms: float = 10.0
    release_ms: float = 120.0
    knee_db: float = 6.0
    makeup_db: float = 0.0
    detector: str = "peak"


def settings_from_parameters(parameters):
    if not parameters:
        return CompressorSettings()
    if (
        not isinstance(parameters, dict)
        or parameters.get("kind", "compressor-v1") != "compressor-v1"
    ):
        raise ValueError("Unsupported dynamics recipe; saved settings are retained.")
    if set(parameters) - {"kind", *asdict(CompressorSettings())}:
        raise ValueError("Unsupported compressor settings; saved settings are retained.")
    try:
        return CompressorSettings(
            **{key: value for key, value in parameters.items() if key != "kind"}
        )
    except TypeError as error:
        raise ValueError("Invalid saved compressor settings.") from error


def compressor_parameters(settings):
    return {"kind": "compressor-v1", **asdict(settings)}


def validate_settings(settings):
    fields = (
        settings.threshold_db,
        settings.ratio,
        settings.attack_ms,
        settings.release_ms,
        settings.knee_db,
        settings.makeup_db,
    )
    if any(
        not isinstance(value, (int, float, np.number))
        or isinstance(value, (bool, np.bool_))
        or not np.isfinite(value)
        for value in fields
    ):
        raise ValueError("Compressor settings must be finite numbers.")
    if not -60 <= settings.threshold_db <= 0 or not 1 <= settings.ratio <= 20:
        raise ValueError("Compressor threshold must be -60 to 0 dBFS and ratio 1 to 20.")
    if not 0 <= settings.attack_ms <= 200 or not 1 <= settings.release_ms <= 3000:
        raise ValueError("Compressor attack must be 0 to 200 ms and release 1 to 3000 ms.")
    if not 0 <= settings.knee_db <= 24 or not -18 <= settings.makeup_db <= 18:
        raise ValueError("Compressor knee must be 0 to 24 dB and makeup -18 to 18 dB.")
    if settings.detector not in ("peak", "rms"):
        raise ValueError("Compressor detector must be peak or RMS.")


def gain_reduction_db(level_db, settings):
    """Negative static gain in dB; the quadratic knee joins at both edges."""
    level = np.asarray(level_db, dtype=float)
    distance = level - settings.threshold_db
    slope = 1 - 1 / settings.ratio
    half = settings.knee_db / 2
    if settings.knee_db == 0:
        reduction = -slope * np.maximum(distance, 0)
    else:
        reduction = np.where(
            distance <= -half,
            0,
            np.where(
                distance >= half,
                -slope * distance,
                -slope * (distance + half) ** 2 / (2 * settings.knee_db),
            ),
        )
    return float(reduction) if level.ndim == 0 else reduction


class CompressorProcessor:
    """Stateful block processor; coefficient updates and reset are offline operations."""

    latency_samples = 0

    def prepare(self, sample_rate, channels, max_block_size):
        if (
            type(sample_rate) is not int
            or type(channels) is not int
            or type(max_block_size) is not int
            or min(sample_rate, channels, max_block_size) <= 0
            or channels not in (1, 2)
        ):
            raise ValueError("Invalid compressor preparation format.")
        self.rate, self.channels, self.max_block_size = sample_rate, channels, max_block_size
        self.set_settings(CompressorSettings())

    def set_settings(self, settings):
        validate_settings(settings)
        self.settings = settings
        self.attack = exp(-1000 / (self.rate * settings.attack_ms)) if settings.attack_ms else 0
        self.release = exp(-1000 / (self.rate * settings.release_ms))
        self.reset()

    def reset(self):
        self.envelope = 0.0
        self.last_detector_db = np.empty(0)
        self.last_reduction_db = np.empty(0)

    def process_block(self, block):
        block = np.asarray(block, dtype=float)
        if (
            block.ndim != 2
            or block.shape[1] != self.channels
            or not 0 < len(block) <= self.max_block_size
            or not np.all(np.isfinite(block))
        ):
            raise ValueError("Block differs from the prepared compressor format or size.")
        output = np.empty_like(block)
        self.last_detector_db = np.empty(len(block))
        self.last_reduction_db = np.empty(len(block))
        settings = self.settings
        slope = 1 - 1 / settings.ratio
        half = settings.knee_db / 2
        makeup = settings.makeup_db
        for i, frame in enumerate(block):
            if settings.detector == "peak":
                detected = max(abs(value) for value in frame)
            else:
                detected = sqrt(sum(value * value for value in frame) / self.channels)
            coefficient = self.attack if detected > self.envelope else self.release
            self.envelope = coefficient * self.envelope + (1 - coefficient) * detected
            level = 20 * log10(max(self.envelope, 1e-12))
            distance = level - settings.threshold_db
            if distance <= -half:
                reduction = 0.0
            elif settings.knee_db and distance < half:
                reduction = -slope * (distance + half) ** 2 / (2 * settings.knee_db)
            else:
                reduction = -slope * distance
            output[i] = frame * (10 ** ((reduction + makeup) / 20))
            self.last_detector_db[i] = level
            self.last_reduction_db[i] = reduction
        return output


@dataclass(frozen=True)
class CompressionResult:
    audio: np.ndarray
    time: np.ndarray
    detector_db: np.ndarray
    reduction_db: np.ndarray
    max_reduction_db: float


def render_compressor(audio, rate, settings, block_size=4096):
    audio = validate_audio(audio)
    validate_settings(settings)
    if type(block_size) is not int or block_size <= 0:
        raise ValueError("Compressor block size must be a positive integer.")
    processor = CompressorProcessor()
    processor.prepare(rate, audio.shape[1], block_size)
    processor.set_settings(settings)
    stride = max(1, round(rate / 100))
    indices = np.arange(0, len(audio), stride)
    detector = np.empty(len(indices))
    reduction = np.empty(len(indices))
    output = np.empty_like(audio)
    max_reduction = 0.0
    for start in range(0, len(audio), block_size):
        block = audio[start : start + block_size]
        output[start : start + len(block)] = processor.process_block(block)
        max_reduction = max(max_reduction, -float(np.min(processor.last_reduction_db)))
        first = (start + stride - 1) // stride
        last = (start + len(block) - 1) // stride + 1
        offsets = indices[first:last] - start
        detector[first:last] = processor.last_detector_db[offsets]
        reduction[first:last] = processor.last_reduction_db[offsets]
    return CompressionResult(output, indices / rate, detector, reduction, max_reduction)
