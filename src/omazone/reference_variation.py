"""Empirical within-passage variation. Display data, never an EQ prescription.

Version 1 uses equal-weight non-overlapping one-second excerpts. Each excerpt's
Welch PSD is integrated into approximately third-octave bands and normalised by
its full-spectrum power. Histograms describe relative power density, not a
confidence interval or a population of independent reference songs.
"""

import json
from copy import deepcopy
from dataclasses import dataclass

import numpy as np
from scipy import signal

from .engine import Spectrum, validate_audio

LEGACY_KIND = "reference-variation-v1"
KIND = "reference-variation-v2"
MIN_WINDOWS = 4
MAX_WINDOWS = 4096
FLOOR_DB = -120
DB_EDGES = np.arange(FLOOR_DB, 1, dtype=float)
POLICY = {
    "excerpt_seconds": 1,
    "excerpt_hop_seconds": 1,
    "fft_seconds": 0.25,
    "fft_samples_even": True,
    "max_fft_samples": 32768,
    "fft_window": "hann",
    "fft_overlap": 0.5,
    "detrend": "constant",
    "bands_per_octave": 3,
    "normalisation": "full-spectrum-unit-power-density",
    "weighting": "equal-accepted-excerpts",
    "absolute_gate_db": -90,
    "relative_gate_db": -40,
    "floor_db": FLOOR_DB,
    "histogram_bin_db": 1,
    "quantiles": [0.1, 0.5, 0.9],
    "quantile_method": "linear",
    "minimum_windows": MIN_WINDOWS,
    "maximum_windows": MAX_WINDOWS,
    "minimum_band_coverage": 0.8,
}
LEGACY_POLICY = deepcopy(POLICY)
POLICY = {**POLICY, "retained_observations": "accepted-excerpt-relative-db-hz"}


def band_edges(rate):
    high = min(20000, rate / 2)
    bands = max(1, int(np.floor(np.log2(high / 20) * 3)))
    return np.geomspace(20, high, bands + 1)


def fft_samples(rate):
    # An odd 250ms length at 44.1 kHz would round the hop upward, dropping
    # an entire seventh Welch frame for the sake of three excess samples.
    return max(2, min(2 * round(rate / 8), 32768))


def band_density(spectrum, edges):
    """Band-averaged density in relative dB/Hz, with fractional FFT-bin overlap.

    Piecewise-constant PSD integration, not point sampling of narrow harmonics.
    Unsupported bands remain NaN for plotting, not a fictional quiet reading.
    """
    frequency, power = spectrum.frequency, spectrum.power
    spacing = frequency[1] - frequency[0]
    boundaries = np.r_[frequency[0], (frequency[:-1] + frequency[1:]) / 2, frequency[-1]]
    cumulative = np.r_[0, np.cumsum(power * np.diff(boundaries))]
    total = max(float(cumulative[-1]), np.finfo(float).tiny)
    energy = np.diff(np.interp(edges, boundaries, cumulative))
    density = energy / np.diff(edges) / total
    values = 10 * np.log10(np.maximum(density, 10 ** (FLOOR_DB / 10)))
    values[edges[1:] > frequency[-1] + spacing * 1e-9] = np.nan
    return values


@dataclass(frozen=True)
class ReferenceVariation:
    rate: int
    frames: int
    sampled_windows: int
    accepted_windows: int
    edges: np.ndarray
    lower_db: np.ndarray
    median_db: np.ndarray
    upper_db: np.ndarray
    valid: np.ndarray
    counts: np.ndarray
    band_counts: np.ndarray
    observations_db: np.ndarray | None = None

    @property
    def frequency(self):
        return np.sqrt(self.edges[:-1] * self.edges[1:])

    @property
    def available(self):
        return bool(np.any(self.valid))


def excerpt_observations(audio, rate, edges):
    """Bounded, equal-weight accepted rows on a caller-supplied band grid."""
    audio = validate_audio(audio)
    if type(rate) is not int or rate <= 40 or audio.shape[1] not in (1, 2):
        raise ValueError("Reference variation needs mono/stereo audio above 40 Hz sample rate.")
    candidates = len(audio) // rate
    if candidates < MIN_WINDOWS:
        raise ValueError("Range analysis needs at least four full seconds of audio.")
    starts = np.linspace(0, candidates - 1, min(candidates, MAX_WINDOWS), dtype=int) * rate
    size = fft_samples(rate)
    rows = np.empty((len(starts), len(edges) - 1))
    energies = np.empty(len(starts))
    for index, start in enumerate(starts):
        frequency, power = signal.welch(
            audio[start : start + rate],
            fs=rate,
            window="hann",
            nperseg=size,
            noverlap=size // 2,
            detrend="constant",
            axis=0,
        )
        power = np.mean(power, axis=1)  # Do not cancel out-of-phase stereo by summing it.
        rows[index] = band_density(Spectrum(frequency, power), edges)
        energies[index] = np.sum(power) * (frequency[1] - frequency[0])
    gate = max(10 ** (-90 / 10), float(np.max(energies)) * 10 ** (-40 / 10))
    return rows[energies > gate], len(starts)


def analyse_variation(audio, rate):
    audio = validate_audio(audio)
    if type(rate) is not int or rate <= 40 or audio.shape[1] not in (1, 2):
        raise ValueError("Reference variation needs mono/stereo audio above 40 Hz sample rate.")
    if rate < 100 or len(audio) // rate < MIN_WINDOWS:
        return None
    edges = band_edges(rate)
    rows, sampled = excerpt_observations(audio, rate, edges)
    size = fft_samples(rate)
    good = np.isfinite(rows) & (rows > FLOOR_DB)
    band_counts = np.sum(good, axis=0)
    # Reject under-resolved bands and bands mostly absent/below the display floor.
    valid = (
        (band_counts >= MIN_WINDOWS)
        & (band_counts >= 0.8 * len(rows))
        & (np.diff(edges) >= rate / size)
    )
    bounds = np.zeros((3, len(edges) - 1))
    counts = np.zeros((len(edges) - 1, len(DB_EDGES) - 1), dtype=np.int64)
    for band in range(len(edges) - 1):
        values = rows[good[:, band], band]
        counts[band] = np.histogram(values, bins=DB_EDGES)[0]
        if valid[band]:
            bounds[:, band] = np.quantile(values, [0.1, 0.5, 0.9], method="linear")
    result = ReferenceVariation(
        rate, len(audio), sampled, len(rows), edges, *bounds, valid, counts, band_counts, rows
    )
    validate_variation(result)
    return result


def validate_variation(value):
    if (
        type(value.rate) is not int
        or value.rate < 100
        or type(value.frames) is not int
        or value.frames < MIN_WINDOWS * value.rate
    ):
        raise ValueError("Invalid reference-variation audio format.")
    expected = min(value.frames // value.rate, MAX_WINDOWS)
    if (
        type(value.sampled_windows) is not int
        or value.sampled_windows != expected
        or type(value.accepted_windows) is not int
        or not 0 <= value.accepted_windows <= expected
    ):
        raise ValueError("Invalid reference-variation observation counts.")
    edges = band_edges(value.rate)
    bands = len(edges) - 1
    if (
        value.edges.shape != edges.shape
        or not np.all(np.isfinite(value.edges))
        or not np.allclose(value.edges, edges, rtol=1e-12, atol=1e-12)
    ):
        raise ValueError("Invalid reference-variation frequency bands.")
    for bounds in (value.lower_db, value.median_db, value.upper_db):
        if (
            bounds.shape != (bands,)
            or not np.all(np.isfinite(bounds))
            or np.any((bounds < FLOOR_DB) | (bounds > 0))
        ):
            raise ValueError("Invalid reference-variation quantiles.")
    if (
        value.valid.shape != (bands,)
        or value.valid.dtype.kind != "b"
        or value.counts.shape != (bands, len(DB_EDGES) - 1)
        or value.band_counts.shape != (bands,)
        or value.counts.dtype.kind not in "iu"
        or value.band_counts.dtype.kind not in "iu"
        or np.any(value.counts < 0)
        or np.any(value.counts > value.accepted_windows)
        or np.any(value.band_counts < 0)
        or np.any(value.band_counts > value.accepted_windows)
        or not np.array_equal(np.sum(value.counts, axis=1), value.band_counts)
    ):
        raise ValueError("Invalid reference-variation density counts.")
    size = fft_samples(value.rate)
    valid = (
        (value.band_counts >= MIN_WINDOWS)
        & (value.band_counts >= 0.8 * value.accepted_windows)
        & (np.diff(value.edges) >= value.rate / size)
    )
    if not np.array_equal(value.valid, valid):
        raise ValueError("Invalid reference-variation support mask.")
    if (
        np.any(value.lower_db > value.median_db)
        or np.any(value.median_db > value.upper_db)
        or any(
            np.any(bounds[~valid] != 0)
            for bounds in (value.lower_db, value.median_db, value.upper_db)
        )
    ):
        raise ValueError("Invalid reference-variation range ordering.")
    if value.observations_db is not None:
        rows = value.observations_db
        if (
            rows.shape != (value.accepted_windows, bands)
            or not np.all(np.isfinite(rows))
            or np.any((rows < FLOOR_DB) | (rows > 0))
        ):
            raise ValueError("Invalid reference-variation excerpt observations.")
        for band in range(bands):
            values = rows[rows[:, band] > FLOOR_DB, band]
            if not np.array_equal(np.histogram(values, bins=DB_EDGES)[0], value.counts[band]):
                raise ValueError("Reference observations do not match density counts.")
            if valid[band] and not np.allclose(
                np.quantile(values, [0.1, 0.5, 0.9], method="linear"),
                [value.lower_db[band], value.median_db[band], value.upper_db[band]],
                rtol=0,
                atol=1e-10,
            ):
                raise ValueError("Reference observations do not match quantiles.")


def variation_dict(value):
    if value is None:
        return None
    validate_variation(value)
    return {
        "kind": KIND if value.observations_db is not None else LEGACY_KIND,
        "policy": deepcopy(POLICY if value.observations_db is not None else LEGACY_POLICY),
        **{
            name: getattr(value, name).tolist()
            if isinstance(getattr(value, name), np.ndarray)
            else getattr(value, name)
            for name in value.__dataclass_fields__
            if name != "observations_db" or value.observations_db is not None
        },
    }


def parse_variation(data):
    if data is None:
        return None
    legacy = isinstance(data, dict) and data.get("kind") == LEGACY_KIND
    fields = set(ReferenceVariation.__dataclass_fields__) - (
        {"observations_db"} if legacy else set()
    )
    if (
        not isinstance(data, dict)
        or set(data) != {"kind", "policy", *fields}
        or data["kind"] not in (KIND, LEGACY_KIND)
    ):
        raise ValueError("Unsupported reference-variation format or policy.")
    try:
        if json.dumps(data["policy"], sort_keys=True, allow_nan=False) != json.dumps(
            LEGACY_POLICY if legacy else POLICY, sort_keys=True
        ):
            raise ValueError("Unsupported reference-variation analysis policy.")
        if (
            not isinstance(data["edges"], list)
            or not 2 <= len(data["edges"]) <= 30
            or not isinstance(data["counts"], list)
            or len(data["counts"]) != len(data["edges"]) - 1
        ):
            raise ValueError("Invalid reference-variation array sizes.")
        if (
            any(
                not isinstance(row, list)
                or len(row) != len(DB_EDGES) - 1
                or any(type(item) is not int for item in row)
                for row in data["counts"]
            )
            or not isinstance(data["band_counts"], list)
            or any(type(item) is not int for item in data["band_counts"])
        ):
            raise ValueError("Reference density counts must be integers.")
        # Do not coerce fractional counts or boolean integers into valid metadata.
        for name in ("counts", "band_counts"):
            array = np.asarray(data[name])
            if array.dtype.kind not in "iu":
                raise ValueError("Reference density counts must be integers.")
        if not legacy:
            rows = data["observations_db"]
            accepted = data["accepted_windows"]
            if (
                type(accepted) is not int
                or not 0 <= accepted <= MAX_WINDOWS
                or not isinstance(rows, list)
                or len(rows) != accepted
                or any(
                    not isinstance(row, list)
                    or len(row) != len(data["edges"]) - 1
                    or any(type(item) not in (int, float) for item in row)
                    for row in rows
                )
            ):
                raise ValueError("Invalid reference excerpt observation array.")
        value = ReferenceVariation(
            data["rate"],
            data["frames"],
            data["sampled_windows"],
            data["accepted_windows"],
            *(
                np.asarray(data[name])
                for name in (
                    "edges",
                    "lower_db",
                    "median_db",
                    "upper_db",
                    "valid",
                    "counts",
                    "band_counts",
                )
            ),
            None
            if legacy
            else np.asarray(data["observations_db"], dtype=float).reshape(
                data["accepted_windows"], len(data["edges"]) - 1
            ),
        )
        validate_variation(value)
        return value
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid reference-variation data.") from error
