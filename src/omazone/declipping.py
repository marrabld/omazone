"""Short-gap Hermite reconstruction baseline, not guaranteed waveform recovery.

Only explicitly accepted plateau intervals are changed. Slopes are estimated
from contiguous intact context, and all fitting uses the original audio.
"""

from dataclasses import dataclass

import numpy as np

from .clipping import ClipInterval, ClipReport, detect_clipping
from .engine import validate_audio


@dataclass(frozen=True)
class RepairSettings:
    max_run_ms: float = 1.0
    context_samples: int = 8
    max_peak_ratio: float = 4.0


@dataclass(frozen=True)
class RejectedRepair:
    interval: ClipInterval
    reason: str


@dataclass(frozen=True)
class RepairResult:
    audio: np.ndarray
    repaired: tuple[ClipInterval, ...]
    rejected: tuple[RejectedRepair, ...]

    @property
    def changed_samples(self):
        return sum(item.end - item.start for item in self.repaired)


def endpoint_slope(values, at_right):
    """Quadratic least-squares fit constrained to the actual endpoint value."""
    if at_right:
        x = np.arange(1 - len(values), 1, dtype=float)
        y = values - values[-1]
    else:
        x = np.arange(len(values), dtype=float)
        y = values - values[0]
    slope, _ = np.linalg.lstsq(np.column_stack((x, x * x)), y, rcond=None)[0]
    return float(slope)


def repair_clipping(audio, sample_rate, report: ClipReport, accepted, settings=None):
    settings = settings if settings is not None else RepairSettings()
    original = validate_audio(audio)
    if type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError("Sample rate must be a positive integer.")
    if not np.isfinite(settings.max_run_ms) or settings.max_run_ms <= 0:
        raise ValueError("Maximum repair duration must be finite and positive.")
    if type(settings.context_samples) is not int or not 3 <= settings.context_samples <= 64:
        raise ValueError("Context must contain 3-64 intact samples on each side.")
    if not np.isfinite(settings.max_peak_ratio) or settings.max_peak_ratio < 1:
        raise ValueError("Peak bound must be a finite ratio of at least one.")
    accepted = tuple(accepted)
    if not accepted:
        raise ValueError("Check at least one suspected plateau interval to repair.")
    # Validate both the candidate origin and its continued presence in the source.
    # Over-range samples alone are deliberately ineligible.
    allowed = set(report.candidates)
    current = set(detect_clipping(original, report.region, report.settings).candidates)
    if len(set(accepted)) != len(accepted):
        raise ValueError("Repair intervals must not be duplicated.")
    if not set(accepted) <= allowed or not set(accepted) <= current:
        raise ValueError("Accepted intervals must be current plateau candidates from this source.")
    output = original.copy()
    repaired, rejected = [], []
    maximum = max(1, int(np.floor(sample_rate * settings.max_run_ms / 1000)))
    context = settings.context_samples
    rails = report.settings
    for interval in sorted(accepted, key=lambda item: (item.channel, item.start)):
        start, end = interval.start, interval.end
        reason = None
        if end - start > maximum:
            reason = "Run exceeds maximum repair duration"
        elif start < context or end + context > len(original):
            reason = "Not enough intact context at the file boundary"
        else:
            left = original[start - context : start, interval.channel]
            right = original[end : end + context, interval.channel]
            context_values = np.concatenate((left, right))
            if np.any(context_values >= rails.positive - rails.tolerance) or np.any(
                context_values <= rails.negative + rails.tolerance
            ):
                reason = "Context reaches a clipping rail; widen selection or reduce context"
            else:
                entering = endpoint_slope(left, at_right=True)
                leaving = endpoint_slope(right, at_right=False)
                sign = 1 if interval.polarity == "positive" else -1
                bound = (rails.positive if sign == 1 else -rails.negative) * settings.max_peak_ratio
                if entering * sign <= 0 or leaving * sign >= 0:
                    reason = "Context slopes do not support a peak of this polarity"
                else:
                    distance = end - start + 1
                    t = np.arange(1, distance, dtype=float) / distance
                    restored = (
                        (2 * t**3 - 3 * t**2 + 1) * left[-1]
                        + (t**3 - 2 * t**2 + t) * distance * entering
                        + (-2 * t**3 + 3 * t**2) * right[0]
                        + (t**3 - t**2) * distance * leaving
                    )
                    if not np.all(np.isfinite(restored)) or np.max(np.abs(restored)) > bound:
                        reason = "Reconstruction exceeds the finite numerical peak bound"
                    elif sign == 1 and np.any(restored < rails.positive - rails.tolerance):
                        reason = "Reconstruction falls inside the positive clipping rail"
                    elif sign == -1 and np.any(restored > rails.negative + rails.tolerance):
                        reason = "Reconstruction falls inside the negative clipping rail"
                    else:
                        output[start:end, interval.channel] = restored
                        repaired.append(interval)
        if reason is not None:
            rejected.append(RejectedRepair(interval, reason))
    return RepairResult(output, tuple(repaired), tuple(rejected))
