"""Explicit, bounded proposals for existing output gain and limiter settings.

Nothing here changes a project, writes audio, or adjusts audition. Each candidate
is measured over the whole recording at the selected delivery rate before final
quantisation. A proposal is not a promise about perceived quality.
"""

from dataclasses import asdict, dataclass, replace

import numpy as np

from .delivery import DeliverySettings, input_ceiling, measure_converted
from .delivery import validate_settings as validate_delivery
from .delivery_measurements import DeliveryMeasurements, measure_blocks
from .engine import validate_audio
from .limiter import LimiterSettings, render_limiter
from .limiter import validate_settings as validate_limiter
from .sample_rate import converted_blocks, validate_rates

KIND = "loudness-target-v1"
MAX_EVALUATIONS = 6
LOUDNESS_TOLERANCE = 0.1
PEAK_TOLERANCE = 0.01


@dataclass(frozen=True)
class TargetSettings:
    target_lufs: float = -14.0
    ceiling_db: float = -1.0
    mode: str = "gain-only"


def validate_settings(settings):
    if not isinstance(settings, TargetSettings) or settings.mode not in ("gain-only", "limiter"):
        raise ValueError("Choose gain-only targeting or intentional use of the final limiter.")
    if any(
        type(value) not in (int, float) or not np.isfinite(value)
        for value in (settings.target_lufs, settings.ceiling_db)
    ):
        raise ValueError("Loudness target and peak ceiling must be finite numbers.")
    if not -60 <= settings.target_lufs <= 0 or not -24 <= settings.ceiling_db <= 0:
        raise ValueError("Target LUFS must be -60 to 0 and peak ceiling -24 to 0 dBTP.")


def target_parameters(settings):
    validate_settings(settings)
    return {"kind": KIND, **asdict(settings)}


def settings_from_parameters(parameters):
    if parameters is None:
        return TargetSettings()
    if (
        not isinstance(parameters, dict)
        or set(parameters) != {"kind", *asdict(TargetSettings())}
        or parameters["kind"] != KIND
    ):
        raise ValueError("Unsupported loudness-target request; no output choices were changed.")
    settings = TargetSettings(**{key: value for key, value in parameters.items() if key != "kind"})
    validate_settings(settings)
    return settings


@dataclass(frozen=True)
class TargetProposal:
    request: TargetSettings
    baseline: DeliveryMeasurements
    measured: DeliveryMeasurements
    gain_db: float
    limiter: LimiterSettings | None
    reduction_db: float
    evaluations: int
    converged: bool
    reason: str


def check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise RuntimeError("Loudness targeting cancelled.")


def meets_peak(reading, target, delivery):
    return (
        reading.true_peak_db is not None
        and reading.true_peak_db <= target.ceiling_db + PEAK_TOLERANCE
        and reading.peak
        <= min(input_ceiling(delivery), 10 ** ((target.ceiling_db + PEAK_TOLERANCE) / 20))
    )


def target_met(reading, target, delivery):
    return (
        reading.lufs is not None
        and meets_peak(reading, target, delivery)
        and abs(reading.lufs - target.target_lufs) <= LOUDNESS_TOLERANCE
    )


def evaluate_candidate(audio, rate, delivery, gain_db, limiter, cancelled=None):
    check_cancelled(cancelled)
    target_rate = delivery.sample_rate or rate
    gain = 10 ** (gain_db / 20)
    if limiter is None:
        # Static gain commutes with linear SRC. Meter scaled converted blocks
        # to avoid another full-length gain buffer; re-render verifies the recipe.
        def blocks():
            for block in converted_blocks(audio, rate, target_rate):
                check_cancelled(cancelled)
                yield block * gain

        reading = measure_blocks(blocks(), target_rate, audio.shape[1])
        reduction = 0.0
    else:
        result = render_limiter(audio * gain, rate, limiter, cancelled=cancelled)
        reading = measure_converted(result.audio, rate, target_rate, cancelled)
        reduction = result.max_reduction_db
    check_cancelled(cancelled)
    return reading, reduction


def propose_target(
    audio,
    rate,
    target=None,
    delivery=None,
    limiter=None,
    baseline=None,
    cancelled=None,
    progress=None,
):
    target = target or TargetSettings()
    delivery = delivery or DeliverySettings()
    limiter = limiter or LimiterSettings()
    validate_settings(target)
    validate_delivery(delivery)
    validate_limiter(limiter)
    audio = validate_audio(audio)
    target_rate = delivery.sample_rate or rate
    validate_rates(rate, target_rate)
    check_cancelled(cancelled)
    if progress:
        progress("Measuring the whole pre-output recording at the delivery rate…")
    baseline = baseline or measure_converted(audio, rate, target_rate, cancelled)
    if (
        baseline.rate != target_rate
        or baseline.frames != (len(audio) * target_rate + rate - 1) // rate
    ):
        raise ValueError("Baseline measurement does not describe this delivery.")
    if (
        baseline.lufs is None
        or baseline.true_peak_db is None
        or baseline.peak <= 0
        or not all(
            np.isfinite(value) for value in (baseline.lufs, baseline.true_peak_db, baseline.peak)
        )
    ):
        raise ValueError(
            "Targeting needs at least 400 ms of measurable, non-silent material above the loudness gate. No gain was proposed."
        )
    required = target.target_lufs - baseline.lufs
    safe = target.ceiling_db - max(baseline.true_peak_db, 20 * np.log10(baseline.peak)) - 0.02
    safe = min(safe, 20 * np.log10(input_ceiling(delivery) / baseline.peak) - 0.001)
    safe = np.floor(safe * 10) / 10

    def bounded_gain(value):
        if target.mode == "gain-only":
            value = min(value, safe)
        return float(np.clip(round(value, 1), -36.0, 18.0))

    gain_db = bounded_gain(required)
    source_ceiling = max(-24.0, float(np.floor((target.ceiling_db - 0.05) * 10) / 10))
    best = None
    seen = set()
    reason = "Bounded search ended; this is the closest peak-safe candidate evaluated, not a guaranteed optimum."
    for iteration in range(1, MAX_EVALUATIONS + 1):
        check_cancelled(cancelled)
        limiting = replace(limiter, ceiling_db=source_ceiling) if target.mode == "limiter" else None
        key = (gain_db, source_ceiling if limiting else None)
        if key in seen:
            break
        seen.add(key)
        if progress:
            progress(
                f"Measuring candidate {iteration}/{MAX_EVALUATIONS}: output gain {gain_db:+.1f} dB…"
            )
        reading, reduction = evaluate_candidate(audio, rate, delivery, gain_db, limiting, cancelled)
        if reading.lufs is None:
            reason = "Candidate loudness is undefined after gating. Adjust the request or output manually."
            break
        converged = target_met(reading, target, delivery)
        candidate = TargetProposal(
            target, baseline, reading, gain_db, limiting, reduction, iteration, converged, ""
        )
        if meets_peak(reading, target, delivery) and (
            best is None
            or abs(reading.lufs - target.target_lufs) < abs(best.measured.lufs - target.target_lufs)
        ):
            best = candidate
        if converged:
            return replace(
                candidate,
                reason="Measured candidate is within the requested loudness/peak tolerances. Apply and render to commit it, then check the delivered result.",
            )
        if limiting and not meets_peak(reading, target, delivery):
            over = max(
                (reading.true_peak_db if reading.true_peak_db is not None else target.ceiling_db)
                - target.ceiling_db,
                20 * np.log10(max(reading.peak, 1e-30)) - target.ceiling_db,
                20 * np.log10(max(reading.peak / input_ceiling(delivery), 1e-30)),
            )
            lower = max(-24.0, float(np.floor((source_ceiling - max(0.1, over + 0.05)) * 10) / 10))
            if lower == source_ceiling:
                reason = "The limiter ceiling bound could not provide safe converted peaks. Adjust output manually."
                break
            source_ceiling = lower
        following = bounded_gain(gain_db + float(np.clip(target.target_lufs - reading.lufs, -6, 6)))
        if following == gain_db and (limiting is None or meets_peak(reading, target, delivery)):
            reason = (
                "Peak-safe gain cannot reach the requested loudness without limiting."
                if target.mode == "gain-only" and safe < min(required, 18.0)
                else "Output gain or 0.1 dB control resolution stopped the search; target was not reached."
            )
            break
        gain_db = following
    check_cancelled(cancelled)
    if best is None:
        raise ValueError("No measured peak-safe proposal was found. " + reason)
    return replace(best, evaluations=len(seen), converged=False, reason=reason)
