"""One downward bell with fixed stable poles and stereo-linked band detection.

The unity-peak band-pass B has the same centre frequency and Q as the cut.
Output = dry + (10**(-cut_db/20) - 1) * B(dry). Only the subtraction weight
varies; feedback coefficients never change during a render. At constant gain
this is a bell cut with unity DC/Nyquist and the requested centre attenuation.
"""

from dataclasses import asdict, dataclass
from math import exp

import numpy as np
from scipy import signal

from .compressor import CompressionResult
from .engine import validate_audio


@dataclass(frozen=True)
class DynamicEQSettings:
    frequency: float = 2400.0
    q: float = 1.0
    threshold_db: float = -24.0
    max_cut_db: float = 6.0
    attack_ms: float = 10.0
    release_ms: float = 120.0


def validate_settings(settings, rate=None):
    values = asdict(settings).values()
    if any(
        not isinstance(value, (int, float, np.number))
        or isinstance(value, (bool, np.bool_))
        or not np.isfinite(value)
        for value in values
    ):
        raise ValueError("Dynamic EQ settings must be finite numbers.")
    if not 20 <= settings.frequency <= 20000 or (
        rate is not None and settings.frequency > 0.45 * rate
    ):
        raise ValueError(
            "Dynamic EQ frequency must be 20 Hz to the lower of 20 kHz and 45% of the sample rate."
        )
    if not 0.5 <= settings.q <= 20:
        raise ValueError("Dynamic EQ Q must be 0.5 to 20.")
    if not -60 <= settings.threshold_db <= 0 or not 0 <= settings.max_cut_db <= 18:
        raise ValueError("Dynamic EQ threshold must be -60 to 0 dBFS and maximum cut 0 to 18 dB.")
    if not 0.1 <= settings.attack_ms <= 200 or not 1 <= settings.release_ms <= 3000:
        raise ValueError("Dynamic EQ attack must be 0.1 to 200 ms and release 1 to 3000 ms.")


def dynamic_eq_parameters(settings):
    validate_settings(settings)
    return {"kind": "dynamic-eq-v1", **asdict(settings)}


def settings_from_parameters(parameters, rate=48000):
    if isinstance(parameters, dict) and not parameters:
        return DynamicEQSettings(frequency=min(2400, rate / 4))
    if (
        not isinstance(parameters, dict)
        or set(parameters) != {"kind", *asdict(DynamicEQSettings())}
        or parameters.get("kind") != "dynamic-eq-v1"
    ):
        raise ValueError("Unsupported dynamic EQ recipe; saved settings are retained.")
    settings = DynamicEQSettings(
        **{key: value for key, value in parameters.items() if key != "kind"}
    )
    validate_settings(settings)
    return settings


def bandpass_sos(settings, rate):
    validate_settings(settings, rate)
    omega = 2 * np.pi * settings.frequency / rate
    alpha = np.sin(omega) / (2 * settings.q)
    return np.array([[alpha, 0, -alpha, 1 + alpha, -2 * np.cos(omega), 1 - alpha]]) / (1 + alpha)


def cut_db(level_db, settings):
    """One dB of cut per dB above threshold, bounded by maximum cut."""
    return np.clip(np.asarray(level_db) - settings.threshold_db, 0, settings.max_cut_db)


def response(settings, rate, frequency, reduction_db=None):
    _, band = signal.sosfreqz(bandpass_sos(settings, rate), worN=frequency, fs=rate)
    cut = settings.max_cut_db if reduction_db is None else reduction_db
    return 1 + (10 ** (-cut / 20) - 1) * band


class DynamicEQProcessor:
    latency_samples = 0

    def prepare(self, sample_rate, channels, max_block_size):
        if (
            type(sample_rate) is not int
            or type(channels) is not int
            or type(max_block_size) is not int
            or sample_rate < 100
            or channels not in (1, 2)
            or max_block_size <= 0
        ):
            raise ValueError("Invalid dynamic EQ preparation format.")
        self.rate, self.channels, self.max_block_size = sample_rate, channels, max_block_size
        self.set_settings(DynamicEQSettings(frequency=min(2400, sample_rate / 4)))

    def set_settings(self, settings):
        """Offline only. New frequency/Q resets history, never live automation."""
        self.sos = bandpass_sos(settings, self.rate)
        self.settings = settings
        self.attack = exp(-1000 / (self.rate * settings.attack_ms))
        self.release = exp(-1000 / (self.rate * settings.release_ms))
        self.reset()

    def reset(self):
        self.state = np.zeros((1, 2, self.channels))
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
            raise ValueError("Block differs from the prepared dynamic EQ format or size.")
        band, self.state = signal.sosfilt(self.sos, block, axis=0, zi=self.state)
        detected = np.max(np.abs(band), axis=1)
        envelopes = np.empty(len(block))
        envelope = self.envelope
        for index, value in enumerate(detected.tolist()):
            coefficient = self.attack if value > envelope else self.release
            envelope = coefficient * envelope + (1 - coefficient) * value
            envelopes[index] = envelope
        self.envelope = envelope
        self.last_detector_db = 20 * np.log10(np.maximum(envelopes, 1e-12))
        self.last_reduction_db = -cut_db(self.last_detector_db, self.settings)
        gain = np.power(10.0, self.last_reduction_db / 20)
        output = block + (gain - 1)[:, None] * band
        if not np.all(np.isfinite(output)):
            raise ValueError("Dynamic EQ produced non-finite audio.")
        return output


def render_dynamic_eq(audio, rate, settings, block_size=4096):
    audio = validate_audio(audio)
    validate_settings(settings, rate)
    if type(block_size) is not int or block_size <= 0:
        raise ValueError("Dynamic EQ block size must be a positive integer.")
    processor = DynamicEQProcessor()
    processor.prepare(rate, audio.shape[1], block_size)
    processor.set_settings(settings)
    stride = max(1, round(rate / 100))
    indices = np.arange(0, len(audio), stride)
    detector, reduction = np.empty(len(indices)), np.empty(len(indices))
    output = np.empty_like(audio)
    maximum = 0.0
    for start in range(0, len(audio), block_size):
        block = audio[start : start + block_size]
        output[start : start + len(block)] = processor.process_block(block)
        maximum = max(maximum, -float(np.min(processor.last_reduction_db)))
        first = (start + stride - 1) // stride
        last = (start + len(block) - 1) // stride + 1
        offsets = indices[first:last] - start
        detector[first:last] = processor.last_detector_db[offsets]
        reduction[first:last] = processor.last_reduction_db[offsets]
    return CompressionResult(output, indices / rate, detector, reduction, maximum)
