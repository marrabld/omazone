"""Conservative hard-clipping candidates, not source separation or repair.

Intervals use absolute half-open sample bounds. Flat threshold runs and samples
above full scale are reported separately: floating-point overload is not proof
that waveform peaks have been lost. Threshold estimates are hints from plateaus.
"""

from dataclasses import dataclass

import numpy as np

from .engine import validate_audio
from .waveform import SampleRegion


@dataclass(frozen=True)
class DetectionSettings:
    positive: float = 1.0
    negative: float = -1.0
    tolerance: float = 0.00005
    minimum_run: int = 3
    channel: int | None = None


@dataclass(frozen=True)
class ClipInterval:
    channel: int
    start: int
    end: int
    polarity: str
    level: float


@dataclass(frozen=True)
class ChannelStats:
    channel: int
    peak: float
    above_full_scale_samples: int
    near_full_scale_samples: int
    candidate_samples: int


@dataclass(frozen=True)
class ClipReport:
    region: SampleRegion
    settings: DetectionSettings
    candidates: tuple[ClipInterval, ...]
    overloads: tuple[ClipInterval, ...]
    stats: tuple[ChannelStats, ...]


@dataclass(frozen=True)
class ThresholdHint:
    channel: int
    positive: float | None
    negative: float | None


def runs(mask):
    """Find maximal contiguous True runs, including the first/last samples."""
    edges = np.diff(np.concatenate(([False], mask, [False])).astype(np.int8))
    return zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True)


def selection_channels(audio, region, settings):
    audio = validate_audio(audio)
    if type(region.start) is not int or type(region.end) is not int:
        raise ValueError("Selection bounds must be integer sample indices.")
    if not 0 <= region.start < region.end <= len(audio):
        raise ValueError("Select a nonempty region inside the original audio.")
    if not all(
        np.isfinite(value) for value in (settings.positive, settings.negative, settings.tolerance)
    ):
        raise ValueError("Detection settings must be finite.")
    if settings.positive <= 0 or settings.negative >= 0:
        raise ValueError("Positive threshold must be above zero; negative threshold below zero.")
    if not 0 < settings.tolerance < min(settings.positive, -settings.negative):
        raise ValueError("Tolerance must be positive and smaller than either threshold magnitude.")
    if type(settings.minimum_run) is not int or settings.minimum_run < 2:
        raise ValueError("Minimum plateau length must be an integer of at least two samples.")
    if settings.channel is not None and (
        type(settings.channel) is not int or not 0 <= settings.channel < audio.shape[1]
    ):
        raise ValueError("Selected channel is not present in the audio.")
    channels = range(audio.shape[1]) if settings.channel is None else (settings.channel,)
    return audio[region.start : region.end], channels


def detect_clipping(audio, region, settings=None):
    settings = settings if settings is not None else DetectionSettings()
    selected, channels = selection_channels(audio, region, settings)
    candidates, overloads, stats = [], [], []
    for channel in channels:
        values = selected[:, channel]
        channel_candidates = []
        # A nearly constant selection provides no useful evidence of flattened
        # peaks. Choose more surrounding context instead of diagnosing DC.
        if np.ptp(values) > settings.tolerance:
            for polarity, threshold in (
                ("positive", settings.positive),
                ("negative", settings.negative),
            ):
                mask = np.abs(values - threshold) <= settings.tolerance
                for start, end in runs(mask):
                    if (
                        end - start >= settings.minimum_run
                        and np.ptp(values[start:end]) <= settings.tolerance
                    ):
                        channel_candidates.append(
                            ClipInterval(
                                channel,
                                region.start + int(start),
                                region.start + int(end),
                                polarity,
                                float(np.median(values[start:end])),
                            )
                        )
        channel_candidates.sort(key=lambda item: item.start)
        candidates.extend(channel_candidates)
        over = np.abs(values) > 1.0
        for start, end in runs(over):
            level = float(np.median(values[start:end]))
            overloads.append(
                ClipInterval(
                    channel,
                    region.start + int(start),
                    region.start + int(end),
                    "positive" if level > 0 else "negative",
                    level,
                )
            )
        stats.append(
            ChannelStats(
                channel,
                float(np.max(np.abs(values))),
                int(np.count_nonzero(over)),
                int(np.count_nonzero(np.abs(np.abs(values) - 1.0) <= settings.tolerance)),
                sum(item.end - item.start for item in channel_candidates),
            )
        )
    return ClipReport(region, settings, tuple(candidates), tuple(overloads), tuple(stats))


def suggest_thresholds(audio, region, settings=None):
    """Suggest the highest-magnitude supported plateau for each channel/polarity.

    No hint is returned without flat-run evidence. Clean low-frequency extrema,
    synthesised waveforms, and quantisation can also produce such evidence.
    """
    settings = settings if settings is not None else DetectionSettings()
    selected, channels = selection_channels(audio, region, settings)
    hints = []
    for channel in channels:
        values = selected[:, channel]
        peak = float(np.max(np.abs(values)))
        positive, negative = [], []
        if np.ptp(values) > settings.tolerance:
            flat_edges = np.abs(np.diff(values)) <= settings.tolerance
            for start, edge_end in runs(flat_edges):
                end = edge_end + 1
                if (
                    end - start < settings.minimum_run
                    or np.ptp(values[start:end]) > settings.tolerance
                ):
                    continue
                level = float(np.median(values[start:end]))
                if abs(level) < max(settings.tolerance * 10, peak * 0.5):
                    continue
                if level > 0:
                    positive.append(level)
                elif level < 0:
                    negative.append(level)
        hints.append(
            ThresholdHint(
                channel, max(positive) if positive else None, min(negative) if negative else None
            )
        )
    return tuple(hints)
