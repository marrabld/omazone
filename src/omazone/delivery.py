"""Final file delivery: bit depth, explicit headroom, and optional dither.

This runs only when a file is written. It never touches the saved recipe, the
rendered chain or A/B audition, so what you hear stays what the recipe produced.

Over-range audio is refused rather than clipped. libsndfile clips silently on
integer writes, and a silently clipped master is the worst outcome this tool can
produce, so the caller must attenuate first and confirm.
"""

from dataclasses import dataclass

import numpy as np
import soundfile as sf

from .engine import validate_audio

# Float first: it is the default and the archival choice. Integers follow.
FORMATS = (
    ("float", "32-bit float WAV", "FLOAT"),
    ("pcm_24", "24-bit WAV", "PCM_24"),
    ("pcm_16", "16-bit WAV", "PCM_16"),
)
SUBTYPES = {key: subtype for key, _, subtype in FORMATS}
LABELS = {key: title for key, title, _ in FORMATS}
BITS = {key: 32 if key == "float" else int(subtype.removeprefix("PCM_")) for key, subtype in SUBTYPES.items()}
# Any |sample| at or above this would wrap or clip on an integer write. libsndfile
# clamps rather than erroring, so this must be caught before writing.
CEILING = {"pcm_24": 1.0 - 2.0**-24, "pcm_16": 1.0 - 2.0**-16}
EPSILON = 1e-12


@dataclass(frozen=True)
class DeliverySettings:
    """What to write, not how to process.

    ``dither`` defaults on for integer targets because the reduction to 24 or
    16 bits adds quantisation distortion either way; dither trades that for a
    small noise floor. Float targets have no quantisation step, so the flag is
    ignored rather than applied.
    """

    format: str = "float"
    dither: bool = True
    # Only used by tests and by anyone who needs reproducible output. Leaving it
    # unset uses a fresh generator, so each export is an independent draw.
    seed: int | None = None


def validate_settings(settings):
    if settings.format not in SUBTYPES:
        raise ValueError("Choose 32-bit float, 24-bit or 16-bit delivery.")
    if type(settings.dither) is not bool:
        raise ValueError("Dither must be on or off.")
    if settings.seed is not None and type(settings.seed) is not int:
        raise ValueError("Dither seed must be a whole number or unset.")


def format_label(settings):
    return LABELS[settings.format]


def quantises(settings):
    """Integer targets reduce precision; float targets have no step to dither."""
    return settings.format != "float"


@dataclass(frozen=True)
class DeliveryReport:
    """What the write will do, so Export can state it before writing."""

    peak_dbfs: float
    attenuation_db: float
    dithered: bool
    steps: int
    clipped: bool

    @property
    def fits(self):
        return not self.clipped


def peak_dbfs(audio):
    """Highest absolute sample as dBFS. Silence reports -inf rather than 0."""
    peak = float(np.max(np.abs(validate_audio(audio))))
    return 20 * np.log10(peak) if peak > EPSILON else float("-inf")


def measure(audio, settings):
    """Report the render's headroom and the dither it would receive.

    ``attenuation_db`` is the amount needed for an integer target to fit. It is
    zero for float, which has no ceiling.
    """
    validate_settings(settings)
    peak = peak_dbfs(audio)
    if not quantises(settings):
        return DeliveryReport(peak, 0.0, False, None, False)
    ceiling = CEILING[settings.format]
    # Work in linear terms so the number stays meaningful when the overshoot is
    # a fraction of a dB. Rounding this to 0.00 dB would read as "no change".
    peak_linear = float(np.max(np.abs(validate_audio(audio))))
    attenuation_db = max(0.0, -20 * np.log10(ceiling / max(peak_linear, EPSILON)))
    return DeliveryReport(
        peak,
        attenuation_db,
        bool(settings.dither),
        BITS[settings.format],
        attenuation_db > EPSILON,
    )


def apply_attenuation(audio, attenuation_db):
    """Scale the render so an integer target fits. Returns the attenuated copy."""
    audio = validate_audio(audio)
    if not np.isfinite(attenuation_db) or attenuation_db > EPSILON:
        raise ValueError("Delivery attenuation must be zero or negative.")
    if attenuation_db == 0:
        return audio.copy()
    scaled = audio * 10 ** (attenuation_db / 20)
    if not np.all(np.isfinite(scaled)):
        raise ValueError("Delivery attenuation produced non-finite audio.")
    return scaled


def measured_step(settings):
    """Quantisation step in linear units for an integer target.

    Verified against libsndfile 1.2.2 by encoding a narrow ramp: both 24 and
    16 bit land on the expected 2**-(bits-1) grid. Float has no step.
    """
    if not quantises(settings):
        return 0.0
    return 2.0 ** -(BITS[settings.format] - 1)


def tpdf_noise(shape, amplitude, seed=None):
    """Triangular dither noise in [-amplitude, amplitude].

    Triangular-PDF dither is the standard choice for a single reduction: it
    sums two independent uniform draws because one uniform draw gives
    rectangular dither, which is measurably worse at the same peak level. The
    halves matter, so ``amplitude`` is the true peak of the result.
    """
    generator = np.random.default_rng(seed)
    first = generator.uniform(-1.0, 1.0, shape)
    second = generator.uniform(-1.0, 1.0, shape)
    return (first + second) * (amplitude / 2)


def prepare(audio, settings, attenuation_db=None):
    """Return the samples to write, plus what happened.

    Raises if an integer target would clip and no usable attenuation was given.
    """
    validate_settings(settings)
    report = measure(audio, settings)
    if report.clipped:
        if attenuation_db is None:
            needed = max(report.attenuation_db, 0.1)
            raise ValueError(
                f"Peak is {report.peak_dbfs:+.2f} dBFS, above the "
                f"{format_label(settings)} ceiling. Attenuate by at least "
                f"{needed:.3f} dB, or lower output gain or the limiter ceiling, "
                f"before writing this format."
            )
        audio = apply_attenuation(audio, attenuation_db)
        report = measure(audio, settings)
        if report.clipped:
            raise ValueError(
                f"Attenuating by {attenuation_db:.2f} dB still peaks at "
                f"{report.peak_dbfs:+.2f} dBFS for {format_label(settings)}."
            )
    if not quantises(settings) or not settings.dither:
        return validate_audio(audio), report
    ceiling = CEILING[settings.format]
    peak = float(np.max(np.abs(audio)))
    if peak > ceiling:
        # measure() already refused this, so reaching here means the ceiling is
        # exact and the render is exactly full scale. Leave it alone rather than
        # add noise that cannot fit.
        return validate_audio(audio), report
    step = measured_step(settings)
    # One step of headroom keeps dither from pushing a full-scale sample past the
    # ceiling: the peaks it protects are exactly the ones at risk.
    amplitude = min(step * 0.5, ceiling - peak)
    if amplitude <= 0:
        return validate_audio(audio), report
    return validate_audio(audio + tpdf_noise(audio.shape, amplitude, settings.seed)), report


def write(path, audio, rate, settings, attenuation_db=None):
    """Write one delivery file. Returns the report describing what was written."""
    validate_settings(settings)
    samples, report = prepare(audio, settings, attenuation_db)
    sf.write(path, samples, rate, subtype=SUBTYPES[settings.format])
    return report