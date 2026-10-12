"""True peak reads the reconstructed signal, not the samples you were given."""

import numpy as np
import pytest

from omazone.truepeak import (
    _edge_trim,
    _interpolator_length,
    overshoot_db,
    true_peak,
    true_peak_db,
)

RATE = 48000


def intersample_cosine(rate=RATE, seconds=1.0):
    """A quarter-rate cosine offset by half a sample.

    Its samples land at exactly +-0.7071 while the signal itself reaches 1.0,
    so the answer is known without consulting the implementation: sample peak
    -3.0103 dBFS, true peak exactly 0.0000 dBTP.
    """
    t = np.arange(int(seconds * rate)) / rate
    return (np.cos(2 * np.pi * (rate / 4) * t + np.pi / 4))[:, None]


def sine(frequency, seconds=1.0, rate=RATE, amplitude=1.0, phase=0.0):
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t + phase))[:, None]


def reference_true_peak_db(audio, rate=RATE, factor=32):
    """A high-resolution measurement to check the fast one against."""
    from scipy import signal

    upsampled = signal.resample_poly(
        np.asarray(audio, dtype=np.float64),
        up=factor,
        down=1,
        axis=0,
        window=("kaiser", 14.0),
    )
    trim = 4000
    return 20 * np.log10(float(np.max(np.abs(upsampled[trim:-trim]))))


def test_an_inter_sample_peak_is_found_and_measured_correctly():
    audio = intersample_cosine()
    assert 20 * np.log10(np.max(np.abs(audio))) == pytest.approx(-3.0103, abs=1e-3)
    assert true_peak_db(audio, RATE) == pytest.approx(0.0, abs=0.05)


def test_true_peak_and_sample_peak_can_disagree():
    """The whole point: a sample peak would report this material as safe."""
    audio = intersample_cosine()
    assert overshoot_db(audio, RATE) == pytest.approx(3.0103, abs=0.05)


@pytest.mark.parametrize("frequency", (997, 1000, 4000, 20000))
def test_a_peak_that_lands_on_a_sample_is_not_invented(frequency):
    """Interpolation must never report a peak the signal does not contain."""
    audio = sine(frequency)
    assert true_peak_db(audio, RATE) == pytest.approx(0.0, abs=0.01)


def test_four_times_oversampling_agrees_with_a_fine_measurement():
    """Calibrated tolerance: fast enough for a render, accurate enough to trust."""
    t = np.arange(RATE) / RATE
    mixed = (
        np.cos(2 * np.pi * 12000 * t + np.pi / 4) + 0.7 * np.cos(2 * np.pi * 12500 * t + np.pi / 3)
    )[:, None]
    assert true_peak_db(mixed, RATE) == pytest.approx(reference_true_peak_db(mixed), abs=0.05)


def test_the_edge_of_the_file_is_where_this_goes_wrong():
    """resample_poly pads, and untrimmed that padding invents a peak.

    This case reads 0.6 dB high if the filter's own transient at the file
    boundaries is left in, which would report a fine signal as inter-sample hot.
    """
    assert _edge_trim(4) == (_interpolator_length(4) - 1) // 2
    assert true_peak_db(intersample_cosine(), RATE) < 0.05


def test_chunking_does_not_change_the_answer(monkeypatch):
    """The memory bound is only safe if it is also exact."""
    import omazone.truepeak as module

    audio = np.random.default_rng(7).normal(0, 0.3, (95 * RATE, 2))
    monkeypatch.setattr(module, "BLOCK_SECONDS", 1e9)
    whole = true_peak_db(audio, RATE)
    monkeypatch.setattr(module, "BLOCK_SECONDS", 30.0)
    assert true_peak_db(audio, RATE) == pytest.approx(whole, abs=1e-9)


def test_mono_and_stereo_agree():
    mono = intersample_cosine()
    stereo = np.repeat(mono, 2, axis=1)
    assert true_peak_db(stereo, RATE) == pytest.approx(true_peak_db(mono, RATE), abs=1e-9)


def test_a_signal_above_zero_dbfs_is_reported_rather_than_clamped():
    """Floating-point audio can exceed full scale; the reading must say so."""
    hot = sine(997) * 2.5
    assert true_peak_db(hot, RATE) == pytest.approx(7.9588, abs=0.05)


def test_a_clip_of_samples_far_above_full_scale_is_not_a_limiting_measurement():
    """Two samples at +-1 reconstruct to more than 1, which is the real risk."""
    audio = np.zeros((RATE, 1))
    audio[RATE // 2, 0] = 1.0
    audio[RATE // 2 + 1, 0] = -1.0
    assert true_peak_db(audio, RATE) > 0.0


@pytest.mark.parametrize(
    "audio",
    (
        pytest.param(np.zeros((RATE, 2)), id="silence"),
        pytest.param(np.zeros((RATE // 100, 2)), id="shorter than the minimum"),
    ),
)
def test_true_peak_is_undefined_rather_than_a_number(audio):
    assert true_peak(audio, RATE) is None
    assert true_peak_db(audio, RATE) is None
    assert overshoot_db(audio, RATE) is None


def test_overshoot_is_zero_when_the_peak_is_on_a_sample():
    assert overshoot_db(sine(997), RATE) == pytest.approx(0.0, abs=0.01)


def test_oversampling_factor_is_configurable_but_never_below_one():
    audio = intersample_cosine()
    assert true_peak_db(audio, RATE, oversample=8) == pytest.approx(0.0, abs=0.05)
    with pytest.raises(ValueError):
        true_peak(audio, RATE, oversample=0)


def test_other_sample_rates_are_measured_on_their_own_grid():
    """A 44.1 kHz file must not be measured with 48 kHz assumptions."""
    for rate in (44100, 96000):
        audio = intersample_cosine(rate=rate)
        assert true_peak_db(audio, rate) == pytest.approx(0.0, abs=0.05)


def test_malformed_input_is_rejected():
    with pytest.raises(ValueError):
        true_peak(np.zeros((0, 2)), RATE)
    with pytest.raises(ValueError):
        true_peak(np.zeros((RATE, 2, 2)), RATE)
    with pytest.raises(ValueError):
        true_peak(np.zeros((RATE, 2)), 0)
    with pytest.raises(ValueError):
        true_peak(np.full((RATE, 2), np.nan), RATE)


def test_four_times_oversampling_cannot_always_see_a_small_overshoot():
    """A documented limitation, pinned so a change to the factor is deliberate.

    Four grid points per sample interval cannot catch every reconstruction peak.
    For noise-like material the largest sample usually *is* the largest
    reconstructed value, and the meter correctly reports no overshoot rather
    than inventing one. Signals where it matters - heavily limited or squared
    material - overshoot by several dB and are caught.
    """
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.9, (2 * RATE, 2))
    assert overshoot_db(noise, RATE) == pytest.approx(0.0, abs=0.01)
    # A step edge does overshoot, and is caught.
    step = np.zeros((2 * RATE, 1))
    step[:1000, 0] = 1.0
    assert true_peak_db(step, RATE) > 0.5
