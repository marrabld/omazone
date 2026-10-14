"""Three complementary linear-phase FIR bands with independent linked compression."""

from dataclasses import dataclass, field, replace

import numpy as np
from scipy import signal

from .compressor import (
    CompressorProcessor,
    CompressorSettings,
    compressor_parameters,
)
from .compressor import settings_from_parameters as compressor_settings
from .compressor import validate_settings as validate_compressor
from .engine import FIRProcessor, MatchFilter, validate_audio

BAND_NAMES = ("Low", "Mid", "High")
DEFAULT_TAPS = 2049


@dataclass(frozen=True)
class BandSettings:
    compressor: CompressorSettings = field(default_factory=lambda: CompressorSettings(ratio=1))
    bypassed: bool = False


@dataclass(frozen=True)
class MultibandSettings:
    low_hz: float = 250.0
    high_hz: float = 2500.0
    taps: int = DEFAULT_TAPS
    bands: tuple[BandSettings, ...] = field(default_factory=lambda: tuple(BandSettings() for _ in range(3)))


def default_settings(rate=48000):
    low = max(20.0, min(250.0, rate * 0.1))
    return MultibandSettings(low_hz=low, high_hz=max(low + 0.1, min(2500.0, rate * 0.3)))


def validate_settings(settings, rate=None):
    frequencies = (settings.low_hz, settings.high_hz)
    if any(
        not isinstance(value, (int, float, np.number))
        or isinstance(value, (bool, np.bool_))
        or not np.isfinite(value)
        for value in frequencies
    ):
        raise ValueError("Crossover frequencies must be finite numbers.")
    maximum = min(20000, rate * 0.45) if rate is not None else 20000
    if not 20 <= settings.low_hz < settings.high_hz <= maximum:
        raise ValueError(f"Crossovers must be ordered from 20 to {maximum:g} Hz, below Nyquist.")
    if type(settings.taps) is not int or not 129 <= settings.taps <= 8193 or settings.taps % 2 != 1:
        raise ValueError("Crossover FIR length must be an odd integer from 129 to 8193.")
    if not isinstance(settings.bands, tuple) or len(settings.bands) != 3:
        raise ValueError("Three-band compression needs exactly three bands.")
    for band in settings.bands:
        if not isinstance(band, BandSettings) or type(band.bypassed) is not bool:
            raise ValueError("Invalid band bypass settings.")
        validate_compressor(band.compressor)


def multiband_parameters(settings):
    return {
        "kind": "three-band-v1", "low_hz": settings.low_hz, "high_hz": settings.high_hz,
        "taps": settings.taps,
        "bands": [
            {"compressor": compressor_parameters(band.compressor), "bypassed": band.bypassed}
            for band in settings.bands
        ],
    }


def settings_from_parameters(parameters):
    if not isinstance(parameters, dict) or parameters.get("kind") != "three-band-v1":
        raise ValueError("Unsupported three-band recipe; saved settings are retained.")
    if set(parameters) != {"kind", "low_hz", "high_hz", "taps", "bands"}:
        raise ValueError("Invalid saved crossover settings.")
    try:
        bands = parameters["bands"]
        if not isinstance(bands, list) or len(bands) != 3:
            raise ValueError("Three-band compression needs exactly three bands.")
        if any(not isinstance(band, dict) or set(band) != {"compressor", "bypassed"} for band in bands):
            raise ValueError("Invalid saved band settings.")
        settings = MultibandSettings(
            parameters["low_hz"], parameters["high_hz"], parameters["taps"],
            tuple(BandSettings(compressor_settings(band["compressor"]), band["bypassed"]) for band in bands),
        )
        validate_settings(settings)
        return settings
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid saved three-band recipe.") from error


def crossover_filters(settings, rate):
    validate_settings(settings, rate)
    low = signal.firwin(settings.taps, settings.low_hz, fs=rate, window=("kaiser", 8.6))
    upper = signal.firwin(settings.taps, settings.high_hz, fs=rate, window=("kaiser", 8.6))
    delayed = np.zeros(settings.taps)
    delayed[(settings.taps - 1) // 2] = 1
    return low, upper - low, delayed - upper


class CrossoverProcessor:
    """Two low-pass filters plus a delayed residual, not three unrelated filters."""

    def prepare(self, rate, channels, max_block_size):
        if (
            any(type(value) is not int for value in (rate, channels, max_block_size))
            or min(rate, channels, max_block_size) <= 0
            or channels not in (1, 2)
        ):
            raise ValueError("Invalid crossover preparation format.")
        self.rate, self.channels, self.max_block_size = rate, channels, max_block_size
        self.filters = [FIRProcessor(), FIRProcessor()]
        for processor in self.filters:
            processor.prepare(rate, channels, max_block_size)
        self.set_settings(default_settings(rate))

    def set_settings(self, settings):
        low, mid, _ = crossover_filters(settings, self.rate)
        for processor, coefficients in zip(self.filters, (low, low + mid), strict=True):
            processor.set_filter(MatchFilter(coefficients, np.array([0]), np.array([0]), self.rate))
        self.latency_samples = (settings.taps - 1) // 2
        self.reset()

    def reset(self):
        for processor in self.filters:
            processor.reset()
        self.history = np.zeros((self.latency_samples, self.channels))

    def process_block(self, block):
        block = np.asarray(block, dtype=float)
        if (
            block.ndim != 2 or block.shape[1] != self.channels
            or not 0 < len(block) <= self.max_block_size or not np.all(np.isfinite(block))
        ):
            raise ValueError("Block differs from the prepared crossover format or size.")
        low, upper = (processor.process_block(block) for processor in self.filters)
        delayed = np.concatenate((self.history, block))
        self.history = delayed[len(block):].copy()
        return low, upper - low, delayed[:len(block)] - upper


class MultibandProcessor:
    def prepare(self, rate, channels, max_block_size):
        self.crossover = CrossoverProcessor()
        self.crossover.prepare(rate, channels, max_block_size)
        self.rate, self.channels, self.max_block_size = rate, channels, max_block_size
        self.compressors = [CompressorProcessor() for _ in range(3)]
        for processor in self.compressors:
            processor.prepare(rate, channels, max_block_size)
        self.set_settings(default_settings(rate))

    def set_settings(self, settings):
        validate_settings(settings, self.rate)
        self.settings = settings
        self.crossover.set_settings(settings)
        self.latency_samples = self.crossover.latency_samples
        for processor, band in zip(self.compressors, settings.bands, strict=True):
            processor.set_settings(
                replace(band.compressor, ratio=1, makeup_db=0) if band.bypassed else band.compressor
            )

    def reset(self):
        self.crossover.reset()
        for processor in self.compressors:
            processor.reset()

    def process_block(self, block):
        split = self.crossover.process_block(block)
        self.last_band_audio = tuple(
            processor.process_block(audio)
            for processor, audio in zip(self.compressors, split, strict=True)
        )
        self.last_detector_db = np.column_stack([processor.last_detector_db for processor in self.compressors])
        self.last_reduction_db = np.column_stack([processor.last_reduction_db for processor in self.compressors])
        return sum(self.last_band_audio)


@dataclass(frozen=True)
class MultibandResult:
    audio: np.ndarray
    time: np.ndarray
    detector_db: np.ndarray
    reduction_db: np.ndarray
    band_max_reduction_db: tuple[float, ...]
    latency_samples: int
    solo_audio: tuple[np.ndarray, ...]
    solo_playback: tuple[np.ndarray, ...]

    @property
    def max_reduction_db(self):
        return max(self.band_max_reduction_db)


def render_multiband(audio, rate, settings, block_size=4096):
    audio = validate_audio(audio)
    validate_settings(settings, rate)
    if type(block_size) is not int or block_size <= 0:
        raise ValueError("Multiband block size must be a positive integer.")
    processor = MultibandProcessor()
    processor.prepare(rate, audio.shape[1], block_size)
    processor.set_settings(settings)
    delay = processor.latency_samples
    stride = max(1, round(rate / 100))
    indices = np.arange(0, len(audio), stride) + delay
    output = np.empty_like(audio)
    # Audition-only float32 buffers avoid keeping three extra float64 renders.
    solos = tuple(np.empty(audio.shape, dtype=np.float32) for _ in range(3))
    detector = np.empty((len(indices), 3))
    reduction = np.empty_like(detector)
    maximum = np.zeros(3)
    peaks = np.zeros(3)

    def process(block, start):
        rendered = processor.process_block(block)
        low, high = max(start, delay), min(start + len(block), len(audio) + delay)
        if low < high:
            source = slice(low - start, high - start)
            target = slice(low - delay, high - delay)
            output[target] = rendered[source]
            maximum[:] = np.maximum(maximum, -np.min(processor.last_reduction_db[source], axis=0))
            for index, band in enumerate(processor.last_band_audio):
                solos[index][target] = band[source]
                peaks[index] = max(peaks[index], float(np.max(np.abs(band[source]))))
        first, last = np.searchsorted(indices, (start, start + len(block)))
        offsets = indices[first:last] - start
        detector[first:last] = processor.last_detector_db[offsets]
        reduction[first:last] = processor.last_reduction_db[offsets]

    for start in range(0, len(audio), block_size):
        process(audio[start:start + block_size], start)
    for tail in range(0, delay, block_size):
        process(np.zeros((min(block_size, delay - tail), audio.shape[1])), len(audio) + tail)
    playback = tuple(
        band if peak <= 0.95 else (band * (0.95 / peak)).astype(np.float32)
        for band, peak in zip(solos, peaks, strict=True)
    )
    return MultibandResult(
        output, (indices - delay) / rate, detector, reduction,
        tuple(float(value) for value in maximum), delay, solos, playback,
    )
