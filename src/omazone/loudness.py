"""Integrated loudness per ITU-R BS.1770-4. Read-only; never changes audio.

The meter is offline by design: it runs on rendered arrays in a worker, not in
the playback callback, so a gated block average cannot cause a dropout.

Scope is mono and stereo, which is all the project renders. BS.1770's channel
weights are therefore 1.0 throughout; surround weights are deliberately absent
rather than approximated.

Loudness is a different measurement from a sample peak. A peak says what a
single sample reached; LUFS says what the programme averaged over time, gated so
that silence and pauses do not drag the figure down. Neither implies the other:
a heavily limited track can be loud without being loud at its peaks, and a
single transient can breach 0 dBFS in a quiet programme. True peak is a third,
separate measurement (#44).
"""

import numpy as np
from scipy import signal

# BS.1770-4 gating constants.
ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0
BLOCK_SECONDS = 0.400
HOP_SECONDS = 0.100
# Offset from the weighted mean square to LUFS, fixed by the standard.
LOUDNESS_OFFSET = -0.691

# K-weighting stage 1: the high-frequency shelf modelling the acoustic effect of
# a head. Coefficients are the standard's, recomputed per sample rate.
_SHELF_F0 = 1681.974450955533
_SHELF_G = 3.999843853973347
_SHELF_Q = 0.7071752369554196
# K-weighting stage 2: the RLB high-pass.
_RLB_F0 = 38.13547087602444
_RLB_Q = 0.5003270373238773

# Loudness is defined over at least one full block; anything shorter has no
# gated mean worth reporting.
MINIMUM_SECONDS = BLOCK_SECONDS


def _biquad(f0, gain_db, q, sample_rate):
    """The standard's bilinear-transform coefficients for one shelf stage."""
    k = np.tan(np.pi * f0 / sample_rate)
    shelf = 10 ** (gain_db / 20)
    v0 = 10 ** (gain_db / 40)
    denominator = 1 + k / q + k**2
    return (
        np.array([1 + k / q + k**2, 2 * (k**2 - 1), 1 - k / q + k**2]) / denominator,
        np.array(
            [
                shelf + v0 * k / q + k**2,
                2 * (k**2 - shelf),
                shelf - v0 * k / q + k**2,
            ]
        )
        / denominator,
    )


def _rlb(sample_rate):
    k = np.tan(np.pi * _RLB_F0 / sample_rate)
    denominator = 1 + k / _RLB_Q + k**2
    return (
        np.array([1 + k / _RLB_Q + k**2, 2 * (k**2 - 1), 1 - k / _RLB_Q + k**2]) / denominator,
        np.array([1.0, -2.0, 1.0]),
    )


def _k_weighting_sos(sample_rate):
    """Both K-weighting stages as second-order sections for one sample rate."""
    a_shelf, b_shelf = _biquad(_SHELF_F0, _SHELF_G, _SHELF_Q, sample_rate)
    a_rlb, b_rlb = _rlb(sample_rate)
    return np.stack([np.concatenate([b_shelf, a_shelf]), np.concatenate([b_rlb, a_rlb])])


def k_weighting(audio, sample_rate):
    """Apply the BS.1770 K-weighting filters, returning the same shape.

    One code path for mono and stereo. Filtering along ``axis=0`` matters:
    ``scipy.signal.lfilter`` defaults to the last axis, which would filter across
    channels instead of down the time axis.
    """
    return signal.sosfilt(_k_weighting_sos(sample_rate), audio, axis=0)


def _block_loudness(weighted, sample_rate):
    """Ungated loudness of every 400ms block, stepping 100ms."""
    block = int(round(BLOCK_SECONDS * sample_rate))
    hop = int(round(HOP_SECONDS * sample_rate))
    if len(weighted) < block:
        return np.empty(0)
    # Cumulative sums give every overlapping block in one pass. Squaring through
    # einsum avoids materialising a second full-length array, which on a song is
    # larger than the audio itself.
    total = np.cumsum(np.einsum("ij,ij->i", weighted, weighted, dtype=np.float64))
    starts = np.arange(0, len(weighted) - block + 1, hop)
    last = starts + block - 1
    first = np.where(starts > 0, total[np.maximum(starts - 1, 0)], 0.0)
    # Sum over the window, then over channels with BS.1770's weights of 1.0.
    mean_square = (total[last] - first) / block
    return LOUDNESS_OFFSET + 10 * np.log10(np.maximum(mean_square, 1e-30))


def integrated_lufs(audio, sample_rate):
    """Gated integrated loudness in LUFS, or ``None`` when it is undefined.

    ``None`` is returned for silence, for audio shorter than one gating block,
    and when the relative gate would leave no blocks. Callers must show that as
    an absent measurement rather than as a number, because printing ``-inf`` or
    a floor value would read as a real level.
    """
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim == 1:
        audio = audio[:, None]
    if audio.ndim != 2 or audio.shape[0] == 0:
        raise ValueError("Loudness needs a non-empty mono or stereo block.")
    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive.")
    if len(audio) < MINIMUM_SECONDS * sample_rate:
        return None
    if not audio.any():
        # Exact silence needs no filtering, and near-silence is caught by the
        # absolute gate below rather than by a separate peak scan.
        return None

    block = _block_loudness(k_weighting(audio, sample_rate), sample_rate)
    if len(block) == 0:
        return None

    # Absolute gate, then a relative gate 10 LU below what survived it.
    passed = block[block > ABSOLUTE_GATE_LUFS]
    if len(passed) == 0:
        return None
    relative = LOUDNESS_OFFSET + 10 * np.log10(
        max(float(np.mean(10 ** ((passed - LOUDNESS_OFFSET) / 10))), 1e-30)
    )
    gated = passed[passed > relative + RELATIVE_GATE_LU]
    if len(gated) == 0:
        return None
    mean_square = float(np.mean(10 ** ((gated - LOUDNESS_OFFSET) / 10)))
    return LOUDNESS_OFFSET + 10 * np.log10(max(mean_square, 1e-30))


def loudness_gain_db(source_lufs, output_lufs):
    """The gain that brings ``output`` to ``source`` loudness, or ``None``.

    Preview matching only. Applied to what you hear, never to what is written,
    so an undefined measurement leaves the comparison untouched rather than
    guessing at a level.
    """
    if source_lufs is None or output_lufs is None:
        return None
    return source_lufs - output_lufs


def format_lufs(value):
    """A reading for the measurement row, or a dash when there is none."""
    if value is None:
        return "—"
    return f"{value:+.1f} LUFS"
