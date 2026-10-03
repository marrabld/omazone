"""Spectral matching, independent of GUI and audio-device code.

Audio arrays always have shape (samples, channels). Power spectra are channel
averages, not spectra of a mono sum, so out-of-phase stereo is not cancelled.
"""

from dataclasses import dataclass

import numpy as np
from scipy import signal
from scipy.ndimage import gaussian_filter1d


@dataclass(frozen=True)
class Spectrum:
    frequency: np.ndarray
    power: np.ndarray

    @property
    def relative_db(self):
        total = max(float(np.sum(self.power)), np.finfo(float).tiny)
        return 10 * np.log10(np.maximum(self.power / total, 1e-15))


@dataclass(frozen=True)
class MatchSettings:
    amount: float = 0.5
    smoothing_octaves: float = 0.33
    max_boost_db: float = 6.0
    max_cut_db: float = 6.0
    taps: int = 2049


@dataclass(frozen=True)
class MatchFilter:
    coefficients: np.ndarray
    frequency: np.ndarray
    requested_db: np.ndarray
    sample_rate: int

    @property
    def latency_samples(self):
        return (len(self.coefficients) - 1) // 2

    def response(self):
        frequency, response = signal.freqz(self.coefficients, worN=8192, fs=self.sample_rate)
        return frequency, 20 * np.log10(np.maximum(np.abs(response), 1e-12))


def validate_audio(audio):
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim != 2 or audio.shape[0] < 2 or audio.shape[1] < 1:
        raise ValueError("Audio must have shape (samples, channels), with at least two samples.")
    if not np.all(np.isfinite(audio)):
        raise ValueError("Audio contains non-finite samples.")
    return audio


def analyse(audio, sample_rate):
    audio = validate_audio(audio)
    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive.")
    size = min(8192, len(audio))
    frequency, power = signal.welch(audio, fs=sample_rate, nperseg=size, noverlap=size // 2, axis=0)
    power = np.mean(power, axis=1)
    if np.sum(power) <= 1e-24:
        raise ValueError("Audio is silent or too quiet to analyse.")
    return Spectrum(frequency, power)


def design_match(source, reference, sample_rate, settings=None):
    settings = settings if settings is not None else MatchSettings()
    if not 0 <= settings.amount <= 1:
        raise ValueError("Amount must be between zero and one.")
    if settings.taps < 3 or settings.taps % 2 != 1:
        raise ValueError("FIR tap count must be odd and at least three.")
    if settings.smoothing_octaves < 0 or min(settings.max_boost_db, settings.max_cut_db) < 0:
        raise ValueError("Smoothing and gain limits cannot be negative.")
    nyquist = sample_rate / 2
    high = min(20000.0, nyquist, reference.frequency[-1], source.frequency[-1])
    if high <= 20:
        raise ValueError("Sample rate is too low for matching.")
    # Uniform log-frequency spacing makes smoothing width meaningful in octaves.
    grid = np.geomspace(20, high, 1024)
    source_db = np.interp(grid, source.frequency, source.relative_db)
    reference_db = np.interp(grid, reference.frequency, reference.relative_db)
    # Density bin widths differ when sample rates/FFT sizes differ. Remove that
    # constant offset and any residual level offset using log-band mean centring.
    correction = reference_db - source_db
    correction -= np.mean(correction)
    bins_per_octave = (len(grid) - 1) / np.log2(high / 20)
    sigma = settings.smoothing_octaves * bins_per_octave
    if sigma > 0:
        correction = gaussian_filter1d(correction, sigma=sigma, mode="nearest")
    correction = settings.amount * np.clip(correction, -settings.max_cut_db, settings.max_boost_db)
    # Fade to unity outside the useful matching range rather than correcting DC.
    frequency = np.concatenate(([0], grid, [nyquist]))
    gain_db = np.concatenate(([0], correction, [0]))
    if high == nyquist:
        frequency = frequency[:-1]
        gain_db = gain_db[:-1]
    coefficients = signal.firwin2(settings.taps, frequency, 10 ** (gain_db / 20), fs=sample_rate)
    return MatchFilter(coefficients, frequency, gain_db, sample_rate)


class FIRProcessor:
    """Stateful FFT overlap-add FIR, with fixed coefficients during a render.

    This prototype allocates arrays. A real-time backend can replace it without
    changing the filter specification or offline renderer's block contract.
    """

    def prepare(self, sample_rate, channels, max_block_size):
        if min(sample_rate, channels, max_block_size) <= 0:
            raise ValueError("Preparation parameters must be positive.")
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_block_size = max_block_size
        self.set_filter(MatchFilter(np.ones(1), np.array([0]), np.array([0]), sample_rate))

    def set_filter(self, spec):
        if spec.sample_rate != self.sample_rate:
            raise ValueError("Filter and processor sample rates differ.")
        self.coefficients = np.asarray(spec.coefficients, dtype=float)
        self.latency_samples = spec.latency_samples
        self.reset()

    def reset(self):
        self.tail = np.zeros((len(self.coefficients) - 1, self.channels))

    def process_block(self, block):
        block = np.asarray(block, dtype=float)
        if block.ndim != 2 or block.shape[1] != self.channels:
            raise ValueError("Block channel count differs from prepared processor.")
        if not 0 < len(block) <= self.max_block_size:
            raise ValueError("Invalid block length.")
        result = signal.fftconvolve(block, self.coefficients[:, None], axes=0)
        result[: len(self.tail)] += self.tail
        self.tail = result[len(block) :].copy()
        return result[: len(block)]

    def flush(self):
        tail = self.tail.copy()
        self.reset()
        return tail


def render(audio, spec, block_size=4096):
    """Render through blocks, then compensate linear-phase delay to retain length."""
    audio = validate_audio(audio)
    processor = FIRProcessor()
    processor.prepare(spec.sample_rate, audio.shape[1], block_size)
    processor.set_filter(spec)
    output = np.empty((len(audio) + len(spec.coefficients) - 1, audio.shape[1]))
    for start in range(0, len(audio), block_size):
        block = audio[start : start + block_size]
        output[start : start + len(block)] = processor.process_block(block)
    output[len(audio) :] = processor.flush()
    delay = processor.latency_samples
    return output[delay : delay + len(audio)].copy()


def rms_db(audio):
    return float(20 * np.log10(max(float(np.sqrt(np.mean(np.square(audio)))), 1e-12)))


def peak_db(audio):
    return float(20 * np.log10(max(float(np.max(np.abs(audio))), 1e-12)))


def audition_pair(source, output):
    """RMS-match previews and apply shared headroom; never changes export audio."""
    gain = 10 ** ((rms_db(source) - rms_db(output)) / 20)
    matched = output * gain
    peak = max(np.max(np.abs(source)), np.max(np.abs(matched)), 1e-12)
    attenuation = min(1.0, 0.95 / peak)
    return (source * attenuation).astype(np.float32), (matched * attenuation).astype(np.float32)
