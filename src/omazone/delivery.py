"""Export-only conversion. Processing recipes and audition audio stay unchanged.

PCM uses explicit nearest-even quantisation and full-amplitude TPDF dither.
Left-aligned int32 samples let libsndfile pack 24-bit codes without a second,
platform-dependent floating-point conversion. Over-range inputs are refused.
"""

import math
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import soundfile as sf

from .engine import validate_audio

FORMATS = (
    ("float", "32-bit float WAV", "FLOAT"),
    ("pcm_24", "24-bit WAV", "PCM_24"),
)
SUBTYPES = {key: subtype for key, _, subtype in FORMATS}
LABELS = {key: title for key, title, _ in FORMATS}
PCM_SCALE = 2**23
PCM_STEP = 1 / PCM_SCALE
# Highest positive 24-bit code. The negative endpoint is -1. We deliberately
# use a symmetric input ceiling so stereo gain and either polarity stay safe.
CEILING = {"pcm_24": 1 - PCM_STEP}


@dataclass(frozen=True)
class DeliverySettings:
    format: str = "float"
    dither: bool = True
    seed: int | None = None


@dataclass(frozen=True)
class DeliveryReport:
    peak_dbfs: float
    attenuation_db: float
    dithered: bool
    bits: int | None
    clipped: bool

    @property
    def fits(self):
        return not self.clipped


def validate_settings(settings):
    if (
        not isinstance(settings, DeliverySettings)
        or type(settings.format) is not str
        or settings.format not in SUBTYPES
    ):
        raise ValueError("Choose 32-bit float or 24-bit PCM delivery.")
    if type(settings.dither) is not bool:
        raise ValueError("Dither must be on or off.")
    if settings.seed is not None and (type(settings.seed) is not int or settings.seed < 0):
        raise ValueError("Dither seed must be a non-negative integer or unset.")


def format_label(settings):
    return LABELS[settings.format]


def quantises(settings):
    return settings.format == "pcm_24"


def measured_step(settings):
    """The exact normalised 24-bit quantisation step, not a measured ramp gap."""
    validate_settings(settings)
    return PCM_STEP if quantises(settings) else 0.0


def input_ceiling(settings):
    validate_settings(settings)
    if not quantises(settings):
        return float(np.finfo(np.float32).max)
    # Reserve a full LSB for TPDF's peak, even at the loudest sample. Never
    # reduce or silently turn off dither because a recording has a loud peak.
    return CEILING["pcm_24"] - (PCM_STEP if settings.dither else 0)


def dbfs(peak):
    return 20 * math.log10(peak) if peak > 0 else float("-inf")


def peak_dbfs(audio):
    return dbfs(float(np.max(np.abs(validate_audio(audio)))))


def measure_peak(peak, settings):
    """Build the delivery decision from a cached, unrounded sample peak."""
    validate_settings(settings)
    if not np.isfinite(peak) or peak < 0:
        raise ValueError("Sample peak must be finite and non-negative.")
    ceiling = input_ceiling(settings)
    clipped = peak > ceiling
    attenuation = 20 * (math.log10(peak) - math.log10(ceiling)) if clipped else 0.0
    return DeliveryReport(
        dbfs(peak), attenuation, quantises(settings) and settings.dither,
        24 if quantises(settings) else None, clipped,
    )


def measure(audio, settings):
    return measure_peak(float(np.max(np.abs(validate_audio(audio)))), settings)


def suggested_attenuation(report):
    """Round reduction UP to hundredths of a dB, with no 'attenuate 0.00' prompt."""
    if not report.clipped:
        return 0.0
    return max(0.01, math.ceil(report.attenuation_db * 100) / 100)


def _gain(attenuation_db):
    if (
        not isinstance(attenuation_db, (int, float, np.number))
        or isinstance(attenuation_db, (bool, np.bool_))
        or not np.isrealobj(attenuation_db)
        or not np.isfinite(attenuation_db)
        or attenuation_db > 0
    ):
        raise ValueError("Delivery attenuation must be a finite number at or below zero.")
    gain = 10 ** (float(attenuation_db) / 20)
    # Round a negative gain toward zero to keep exact-boundary attenuation
    # inside the input ceiling despite floating-point arithmetic.
    return float(np.nextafter(gain, 0)) if gain > 0 and attenuation_db < 0 else gain


def apply_attenuation(audio, attenuation_db):
    return validate_audio(audio) * _gain(attenuation_db)


def tpdf_noise(shape, amplitude, seed=None):
    """TPDF with peak +/-amplitude. For standard dither, amplitude is one LSB."""
    rng = np.random.default_rng(seed)
    draws = rng.random((*shape, 2))
    return (draws[..., 0] - draws[..., 1]) * amplitude


def _export_input(audio, settings, attenuation_db):
    validate_settings(settings)
    audio = validate_audio(audio)
    gain = 1.0 if attenuation_db is None else _gain(attenuation_db)
    peak = float(np.max(np.abs(audio))) * gain
    report = measure_peak(peak, settings)
    if report.clipped:
        raise ValueError(
            f"Peak {report.peak_dbfs:+.2f} dBFS exceeds the {format_label(settings)} "
            f"ceiling, including dither headroom. Attenuate by at least "
            f"{suggested_attenuation(report):.2f} dB more, or lower output gain "
            "or the limiter ceiling."
        )
    return audio, gain, report


def _convert(block, settings, gain, rng):
    if not quantises(settings):
        return (block * gain).astype(np.float32)
    scaled = block * gain * PCM_SCALE
    if settings.dither:
        scaled = scaled + tpdf_noise(block.shape, 1.0, rng)
    codes = np.rint(scaled)
    if np.any(codes < -PCM_SCALE) or np.any(codes > PCM_SCALE - 1):
        raise ValueError("Quantised samples exceed the 24-bit range; no file was written.")
    # libsndfile takes the most significant 24 bits of each int32 sample.
    return codes.astype(np.int32) * np.int32(256)


def prepare(audio, settings, attenuation_db=None):
    """Return representable normalised samples and their actual sample-peak report."""
    audio, gain, report = _export_input(audio, settings, attenuation_db)
    samples = _convert(audio, settings, gain, np.random.default_rng(settings.seed))
    if quantises(settings):
        samples = samples.astype(np.float64) / 2**31
    return samples, replace(report, peak_dbfs=dbfs(float(np.max(np.abs(samples)))))


def write(path, audio, rate, settings, attenuation_db=None):
    """Convert in bounded blocks and atomically replace the destination on success.

    Validation completes before opening any output. A failed write leaves an
    existing destination intact. Dither uses one generator across the whole file.
    """
    audio, gain, report = _export_input(audio, settings, attenuation_db)
    if type(rate) is not int or rate <= 0:
        raise ValueError("Sample rate must be a positive integer.")
    path = Path(path)
    rng = np.random.default_rng(settings.seed)
    peak = 0.0
    temporary = None
    try:
        # Close the handle before libsndfile opens it, including on Windows.
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".wav", delete=False) as handle:
            temporary = Path(handle.name)
        with sf.SoundFile(
            temporary, "w", samplerate=rate, channels=audio.shape[1],
            format="WAV", subtype=SUBTYPES[settings.format],
        ) as output:
            for start in range(0, len(audio), 65536):
                block = _convert(audio[start:start + 65536], settings, gain, rng)
                output.write(block)
                value = float(np.max(np.abs(block.astype(np.float64))))
                peak = max(peak, value / 2**31 if quantises(settings) else value)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return replace(report, peak_dbfs=dbfs(peak))
