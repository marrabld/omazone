"""Deterministic static correction toward a measured within-passage range.

Version 1 compares equally weighted excerpt medians/quantiles after re-normalising
each excerpt over common complete, FFT-resolved bands. No bootstrap or dynamics.
"""

import hashlib

import numpy as np
from scipy import signal
from scipy.ndimage import gaussian_filter1d
from scipy.special import logsumexp

from .engine import MatchFilter, MatchSettings, analyse, design_match
from .reference_variation import (
    FLOOR_DB,
    MIN_WINDOWS,
    excerpt_observations,
    fft_samples,
    validate_variation,
)


def range_signature(variation):
    """Canonical binary signature, avoiding large JSON conversions on UI refresh."""
    if variation is None or variation.observations_db is None:
        return None
    digest = hashlib.sha256(b"range-aware-v1")
    for values in (
        np.array([variation.rate, variation.frames]),
        variation.edges,
        variation.observations_db,
    ):
        array = np.asarray(values, dtype="<f8")
        digest.update(str(array.shape).encode("ascii"))
        digest.update(array.tobytes())
    return digest.hexdigest()


def range_unavailable(variation):
    if variation is None or variation.observations_db is None:
        return "Range-aware matching needs excerpt data. Reload the reference or recapture a target of at least four full seconds."
    if not variation.available:
        return "Reference has too few supported observations for range-aware matching. Choose a longer representative passage."
    return None


def normalised_rows(rows, common, widths):
    """Renormalise over the common frequency domain before taking quantiles."""
    values = rows[:, common]
    # Excerpts with no above-floor common-domain energy are unsupported, even
    # when ultrasonic/out-of-domain energy passed the original absolute gate.
    values = values[np.any(values > FLOOR_DB, axis=1)]
    if len(values) < MIN_WINDOWS:
        raise ValueError(
            "Range-aware matching needs four energetic excerpts in the common frequency range."
        )
    log_power = values * (np.log(10) / 10)
    totals = logsumexp(log_power + np.log(widths[common]), axis=1)
    return (log_power - totals[:, None]) * (10 / np.log(10)), values > FLOOR_DB


def design_range_match(audio, rate, variation, settings=None):
    settings = settings or MatchSettings(method="range-aware-v1")
    reason = range_unavailable(variation)
    if reason:
        raise ValueError(reason)
    validate_variation(variation)
    if (
        settings.method != "range-aware-v1"
        or not np.isfinite(settings.amount)
        or not 0 <= settings.amount <= 1
        or type(settings.taps) is not int
        or settings.taps < 3
        or settings.taps % 2 != 1
        or not all(
            np.isfinite(value) and value >= 0
            for value in (settings.smoothing_octaves, settings.max_boost_db, settings.max_cut_db)
        )
    ):
        raise ValueError("Invalid range-aware matching settings.")
    source, _ = excerpt_observations(audio, rate, variation.edges)
    widths = np.diff(variation.edges)
    common = (variation.edges[1:] <= min(rate, variation.rate) / 2) & (
        widths >= max(rate / fft_samples(rate), variation.rate / fft_samples(variation.rate))
    )
    if np.count_nonzero(common) < 4:
        raise ValueError("Too few common resolved frequency bands for range-aware matching.")
    src, source_good = normalised_rows(source, common, widths)
    ref, reference_good = normalised_rows(variation.observations_db, common, widths)
    # Do not amplify numerically present but effectively empty tails/noise.
    # This is a conservative relative density gate, not source separation.
    source_good &= src >= np.max(src, axis=1)[:, None] - 60
    reference_good &= ref >= np.max(ref, axis=1)[:, None] - 60
    support = (source_good.sum(axis=0) >= np.maximum(MIN_WINDOWS, 0.8 * len(src))) & (
        reference_good.sum(axis=0) >= np.maximum(MIN_WINDOWS, 0.8 * len(ref))
    )
    if not np.any(support):
        raise ValueError("No shared energetic bands support range-aware correction.")
    correction = np.zeros(np.count_nonzero(common))
    for band in np.flatnonzero(support):
        source_median = np.median(src[source_good[:, band], band])
        lower, upper = np.quantile(ref[reference_good[:, band], band], [0.1, 0.9], method="linear")
        correction[band] = np.clip(source_median, lower, upper) - source_median
    centres = variation.frequency[common]
    high = min(20000.0, rate / 2, variation.edges[-1])
    grid = np.geomspace(20, high, 1024)
    # Explicit zero anchors outside the analysed band centres, not extrapolated
    # boosts toward DC or Nyquist. Unsupported bands remain zero anchors too.
    requested = np.interp(np.log(grid), np.log(centres), correction, left=0, right=0)
    sigma = settings.smoothing_octaves * (len(grid) - 1) / np.log2(high / 20)
    if sigma:
        requested = gaussian_filter1d(requested, sigma=sigma, mode="constant", cval=0)
    # Fade supported runs to zero at their outer band edges, rather than making
    # a hard gain discontinuity where an empty band interrupts the design.
    # FIR realisation still couples nearby frequencies.
    supported = np.zeros(len(widths), dtype=bool)
    supported[common] = support
    taper = np.zeros(len(grid))
    bands = np.flatnonzero(supported)
    for run in np.split(bands, np.flatnonzero(np.diff(bands) > 1) + 1):
        if not len(run):
            continue
        low, high_edge = np.log2(variation.edges[[run[0], run[-1] + 1]])
        fade = min(1 / 6, (high_edge - low) / 2)
        distance = np.minimum(np.log2(grid) - low, high_edge - np.log2(grid))
        taper += np.sin(np.clip(distance / fade, 0, 1) * np.pi / 2) ** 2
    requested *= taper
    requested *= settings.amount
    requested = np.clip(
        requested, -settings.amount * settings.max_cut_db, settings.amount * settings.max_boost_db
    )
    frequency = np.r_[0.0, grid, rate / 2]
    gain_db = np.r_[0.0, requested, 0.0]
    if high == rate / 2:
        frequency, gain_db = frequency[:-1], gain_db[:-1]
        gain_db[-1] = 0  # No correction at Nyquist.
    coefficients = signal.firwin2(settings.taps, frequency, 10 ** (gain_db / 20), fs=rate)
    return MatchFilter(coefficients, frequency, gain_db, rate)


def design_target_match(audio, rate, target, settings, spectrum=None):
    if settings.method == "range-aware-v1":
        return design_range_match(audio, rate, target.variation, settings)
    return design_match(
        spectrum if spectrum is not None else analyse(audio, rate), target.spectrum, rate, settings
    )
