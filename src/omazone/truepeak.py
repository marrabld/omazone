"""Inter-sample (true) peak, measured on the reconstructed signal.

A sample peak reads the samples you have. A true peak reads what a digital to
analogue converter reconstructs between them, which can be higher. A master can
sit at exactly 0 dBFS sample peak and still clip on playback, and nothing on the
Output step can show you that except this measurement.

The method follows ITU-R BS.1770-4 Annex 2 in shape: oversample by four, filter
with a band-limited interpolator, and take the largest absolute value. The
interpolator is designed for the file's own sample rate rather than reusing the
standard's 48 kHz coefficient tables, because Omazone accepts references and
recordings at any rate and a table for one rate would misreport the rest.

This is a measurement only. Nothing here attenuates, limits, or alters audio,
and it never runs in the audio callback: the caller computes it from rendered
arrays in the render worker, like integrated loudness.
"""

import numpy as np
from scipy import signal

DEFAULT_OVERSAMPLE = 4

# BS.1770-4 Annex 2 specifies four-times oversampling. The method is a band-limited
# interpolation rather than sample repetition, because repeating samples invents
# a staircase that has peaks where the real signal has none.
MINIMUM_SECONDS = 0.05
# Chunking bounds the memory: oversampling a six-minute stereo song by four
# would otherwise allocate about 1.2 GB in one piece.
BLOCK_SECONDS = 30.0


def _interpolator_length(oversample):
    """Length of the polyphase filter, matching what resample_poly designs."""
    return 10 * oversample + 1


def _edge_trim(oversample):
    """Upsampled samples at each end that the filter has not settled over.

    Without this the padding at the array edges produces a peak that the real
    signal does not have: an exact test case measured 0.6 dB high because of it.
    """
    return (_interpolator_length(oversample) - 1) // 2


def true_peak(audio, sample_rate, oversample=DEFAULT_OVERSAMPLE):
    """The largest reconstructed sample, in linear units.

    Returns ``None`` when there is nothing to measure, so a caller can show an
    absent reading rather than a floor value that looks like a real level.
    """
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim == 1:
        audio = audio[:, None]
    if audio.ndim != 2 or audio.shape[0] == 0:
        raise ValueError("True peak needs a non-empty mono or stereo block.")
    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive.")
    if oversample < 1:
        raise ValueError("Oversampling factor must be at least one.")
    if not np.isfinite(audio).all():
        raise ValueError("True peak needs finite samples.")
    if audio.shape[0] < max(1, int(MINIMUM_SECONDS * sample_rate)):
        return None
    if not audio.any():
        return None

    # A Kaiser-windowed low-pass with a deep stop band, so the band above the
    # original Nyquist does not fold back in as a phantom peak.
    window = ("kaiser", 14.0)
    trim = _edge_trim(oversample)
    step = max(1, int(BLOCK_SECONDS * sample_rate))
    overlap = _interpolator_length(oversample)

    peak = 0.0
    for start in range(0, len(audio), step):
        stop = min(len(audio), start + step)
        low = max(0, start - overlap)
        high = min(len(audio), stop + overlap)
        upsampled = signal.resample_poly(
            audio[low:high], up=oversample, down=1, axis=0, window=window
        )
        # resample_poly pads every chunk. Where a chunk edge coincides with the
        # real start or end of the file, that padding is what the filter has not
        # settled over, and keeping it reports a peak the signal does not have.
        # Between chunks the overlap already carries valid context.
        head = trim if low == 0 else 0
        tail = trim if high == len(audio) else 0
        interior = upsampled[head : len(upsampled) - tail] if tail else upsampled[head:]
        if len(interior):
            # The extremes, not abs(): an absolute-value copy of a four-times
            # oversampled song is most of a gigabyte for no extra information.
            peak = max(peak, float(interior.max()), -float(interior.min()))
    return peak or None


def true_peak_db(audio, sample_rate, oversample=DEFAULT_OVERSAMPLE):
    """True peak in dBTP, or ``None`` when undefined.

    dBTP means decibels relative to true-peak full scale. It is not a sample-peak
    figure: the two are reported separately precisely because they disagree.
    """
    peak = true_peak(audio, sample_rate, oversample)
    if peak is None:
        return None
    return 20 * np.log10(max(peak, 1e-30))


def overshoot_db(audio, sample_rate, oversample=DEFAULT_OVERSAMPLE):
    """How far the reconstructed signal rises above the samples, in dB.

    Zero for any signal whose peak lands on a sample. Positive is the
    inter-sample problem a sample peak cannot see.
    """
    sample = float(np.max(np.abs(audio))) if np.asarray(audio).size else 0.0
    if sample <= 0:
        return None
    reconstructed = true_peak(audio, sample_rate, oversample)
    if reconstructed is None:
        return None
    return 20 * np.log10(reconstructed / sample)


def format_true_peak(value):
    """A dBTP reading for the measurement row, or a dash when there is none."""
    if value is None:
        return "— dBTP"
    return f"{value:+.1f} dBTP"
