"""Peak-preserving display data and selection bounds, independent of Qt.

Selections use half-open sample intervals [start, end), matching NumPy slices.
The peak index is built once on loading; zoomed-out redraws never scan a song.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SampleRegion:
    start: int
    end: int

    @classmethod
    def from_seconds(cls, start, end, sample_rate, length):
        first, last = sorted((round(start * sample_rate), round(end * sample_rate)))
        return cls(max(0, min(length, first)), max(0, min(length, last)))


def extrema(audio, stride):
    """Min/max buckets including the last partial bucket, without padding."""
    starts = np.arange(0, len(audio), stride)
    return np.minimum.reduceat(audio, starts, axis=0), np.maximum.reduceat(audio, starts, axis=0)


class PeakIndex:
    def __init__(self, audio, base_stride=256):
        self.audio = audio
        self.levels = []
        low, high = extrema(audio, base_stride)
        stride = base_stride
        while True:
            self.levels.append((stride, low, high))
            if len(low) <= 1:
                break
            starts = np.arange(0, len(low), 2)
            low = np.minimum.reduceat(low, starts, axis=0)
            high = np.maximum.reduceat(high, starts, axis=0)
            stride *= 2
        self.peak = np.maximum(np.abs(low[0]), np.abs(high[0]))

    def visible(self, start, end, max_bins=1600):
        """Return sample positions and channel min/max; equal min/max means raw samples.

        Overview buckets can straddle the viewport edges. They retain extrema
        rather than dropping a peak due to point subsampling.
        """
        length = len(self.audio)
        start = max(0, min(length - 1, int(start)))
        end = max(start + 1, min(length, int(end)))
        span = end - start
        if span <= max_bins * 2:
            raw = self.audio[start:end]
            return np.arange(start, end), raw, raw
        requested = max(1, int(np.ceil(span / max_bins)))
        if requested < self.levels[0][0]:
            low, high = extrema(self.audio[start:end], requested)
            positions = start + np.arange(len(low)) * requested
            positions = np.minimum(positions + (requested - 1) / 2, end - 1)
            return positions, low, high
        stride, low, high = self.levels[-1]
        for level in self.levels:
            if level[0] >= requested:
                stride, low, high = level
                break
        first = start // stride
        last = min(len(low), int(np.ceil(end / stride)))
        positions = np.arange(first, last) * stride + (stride - 1) / 2
        positions = np.clip(positions, start, end - 1)
        return positions, low[first:last], high[first:last]
