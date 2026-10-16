"""Bounded streaming delivery meters using the existing loudness/true-peak policy."""

from dataclasses import dataclass

import numpy as np
from scipy import signal

from .loudness import ABSOLUTE_GATE_LUFS, LOUDNESS_OFFSET, RELATIVE_GATE_LU, _k_weighting_sos
from .truepeak import MINIMUM_SECONDS, _edge_trim, _interpolator_length


@dataclass(frozen=True)
class DeliveryMeasurements:
    rate: int
    frames: int
    peak: float
    true_peak_db: float | None
    lufs: float | None


class StreamMeter:
    """One bounded audio block plus overlap; only scalar loudness blocks persist."""

    def __init__(self, rate, channels):
        self.rate, self.channels = rate, channels
        self.frames, self.peak, self.true_peak = 0, 0.0, 0.0
        self.sos = _k_weighting_sos(rate)
        self.state = np.zeros((len(self.sos), 2, channels))
        self.energy = np.empty(0)
        self.powers = []
        self.pending = np.empty((0, channels))
        self.past = np.empty((0, channels))
        self.first = True

    def feed(self, block):
        if not np.all(np.isfinite(block)):
            raise ValueError("Converted delivery contains non-finite samples.")
        self.frames += len(block)
        self.peak = max(self.peak, float(np.max(np.abs(block))))
        weighted, self.state = signal.sosfilt(self.sos, block, axis=0, zi=self.state)
        energy = np.r_[self.energy, np.einsum("ij,ij->i", weighted, weighted)]
        size, hop = max(1, round(0.4 * self.rate)), max(1, round(0.1 * self.rate))
        if len(energy) >= size:
            totals = np.r_[0.0, np.cumsum(energy)]
            starts = np.arange(0, len(energy) - size + 1, hop)
            self.powers.extend((totals[starts + size] - totals[starts]) / size)
            self.energy = energy[(starts[-1] + hop) :]
        else:
            self.energy = energy
        # Keep enough lookahead to know the real end before trimming its
        # unsettled filter samples, even if the final incoming block is tiny.
        pending = np.concatenate((self.pending, block))
        context = _interpolator_length(4)
        if len(pending) >= 2 * context:
            split = len(pending) - context
            self._peak(pending[:split], pending[split:], final=False)
            self.pending = pending[split:].copy()
        else:
            self.pending = pending

    def _peak(self, current, future, final):
        context = _interpolator_length(4)
        combined = np.concatenate((self.past, current, future[:context]))
        upsampled = signal.resample_poly(combined, 4, 1, axis=0, window=("kaiser", 14.0))
        start = _edge_trim(4) if self.first else len(self.past) * 4
        stop = (len(self.past) + len(current)) * 4 - (_edge_trim(4) if final else 0)
        if stop > start:
            self.true_peak = max(self.true_peak, float(np.max(np.abs(upsampled[start:stop]))))
        self.past = combined[: len(self.past) + len(current)][-context:].copy()
        self.first = False

    def finish(self):
        if len(self.pending):
            self._peak(self.pending, np.empty((0, self.channels)), final=True)
            self.pending = np.empty((0, self.channels))
        powers = np.asarray(self.powers)
        absolute = 10 ** ((ABSOLUTE_GATE_LUFS - LOUDNESS_OFFSET) / 10)
        passed = powers[powers > absolute]
        lufs = None
        if len(passed) and self.channels in (1, 2):
            gate = np.mean(passed) * 10 ** (RELATIVE_GATE_LU / 10)
            gated = passed[passed > gate]
            if len(gated):
                lufs = float(LOUDNESS_OFFSET + 10 * np.log10(np.mean(gated)))
        true_peak = None
        if (
            self.frames >= max(1, int(MINIMUM_SECONDS * self.rate))
            and self.peak > 0
            and self.true_peak > 0
        ):
            true_peak = float(20 * np.log10(self.true_peak))
        return DeliveryMeasurements(self.rate, self.frames, self.peak, true_peak, lufs)


def measure_blocks(blocks, rate, channels):
    meter = StreamMeter(rate, channels)
    for block in blocks:
        meter.feed(block)
    return meter.finish()
