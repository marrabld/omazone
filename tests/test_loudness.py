"""Integrated loudness follows BS.1770, including where it must refuse to answer."""

import numpy as np
import pytest
from scipy import signal

from omazone.loudness import (
    _biquad,
    _rlb,
    format_lufs,
    integrated_lufs,
    k_weighting,
    loudness_gain_db,
)

RATE = 48000


def tone(level_db, seconds=10.0, rate=RATE, frequency=1000.0):
    """A sine at a known level.

    K-weighting is +0.691 dB at 1 kHz, which is what the standard's -0.691
    offset cancels, so a sine with this RMS reads its own dBFS in LUFS.
    """
    t = np.arange(int(seconds * rate)) / rate
    rms = 10 ** (level_db / 20)
    return (rms * np.sqrt(2) * np.sin(2 * np.pi * frequency * t))[:, None]


def k_weighting_db(frequency, rate=RATE):
    a_shelf, b_shelf = _biquad(1681.974450955533, 3.999843853973347, 0.7071752369554196, rate)
    a_rlb, b_rlb = _rlb(rate)
    w, shelf = signal.freqz(b_shelf, a_shelf, worN=16384, fs=rate)
    _, rlb = signal.freqz(b_rlb, a_rlb, worN=16384, fs=rate)
    total = shelf * rlb
    return float(20 * np.log10(np.abs(total[np.argmin(np.abs(w - frequency))])))


@pytest.mark.parametrize("level", (-14.0, -23.0, -33.0, -40.0))
def test_a_known_level_tone_reads_its_own_level(level):
    assert integrated_lufs(tone(level), RATE) == pytest.approx(level, abs=0.05)


def test_loudness_difference_is_measured_not_assumed():
    """A 6 dB gain change must read as 6 LU, which is what makes matching work."""
    quiet = integrated_lufs(tone(-23.0), RATE)
    louder = integrated_lufs(tone(-29.0), RATE)
    assert quiet - louder == pytest.approx(6.0, abs=0.01)


def test_the_k_weighting_curve_matches_the_standard():
    """The published shape: a high shelf near +4 dB and a low roll-off.

    The standard's -0.691 offset implies the curve is +0.691 dB at 1 kHz. Any
    disagreement here means the meter is miscalibrated, not merely approximate.
    """
    assert k_weighting_db(1000) == pytest.approx(0.691, abs=0.01)
    assert k_weighting_db(10000) == pytest.approx(4.0, abs=0.1)
    assert k_weighting_db(50) == pytest.approx(-4.0, abs=0.2)
    assert k_weighting_db(20) < k_weighting_db(50) < 0


def test_a_section_more_than_ten_lu_down_is_left_out():
    """The relative gate is what separates a programme from its quiet passages."""
    t = np.arange(10 * RATE) / RATE
    level = np.where(t >= 8, 0.1, 1.0) * 10 ** (-6.0 / 20) * np.sqrt(2)
    audio = level * np.sin(2 * np.pi * 1000 * t)
    assert integrated_lufs(audio[:, None], RATE) == pytest.approx(-6.0, abs=0.2)


def test_a_passage_below_the_absolute_gate_never_counts():
    t = np.arange(10 * RATE) / RATE
    level = np.where(t >= 8, 0.01, 1.0) * 10 ** (-6.0 / 20) * np.sqrt(2)
    audio = level * np.sin(2 * np.pi * 1000 * t)
    # A naive RMS average would drag this down by the quiet passage; gating must not.
    naive = 20 * np.log10(np.sqrt(np.mean(audio**2)))
    assert integrated_lufs(audio[:, None], RATE) == pytest.approx(-6.0, abs=0.2)
    assert naive < -6.5


def test_stereo_sums_channel_energy_so_equal_channels_read_three_lu_higher():
    mono = tone(-23.0)
    stereo = np.repeat(mono, 2, axis=1)
    assert integrated_lufs(stereo, RATE) == pytest.approx(
        integrated_lufs(mono, RATE) + 3.01, abs=0.05
    )


def test_the_result_does_not_depend_on_the_sample_rate():
    """Same tone, two rates. The filter coefficients are rebuilt per rate."""
    assert integrated_lufs(tone(-23.0, rate=44100), 44100) == pytest.approx(-23.0, abs=0.05)
    assert integrated_lufs(tone(-23.0, rate=96000), 96000) == pytest.approx(-23.0, abs=0.05)


def test_k_weighting_keeps_the_shape_of_the_input():
    audio = np.repeat(tone(-12.0, seconds=2.0), 2, axis=1)
    weighted = k_weighting(audio, RATE)
    assert weighted.shape == audio.shape
    # Mono and stereo must weight identically; only the channel sum differs.
    mono = k_weighting(audio[:, :1], RATE)
    np.testing.assert_allclose(weighted[:, 0], mono[:, 0], atol=1e-12)


@pytest.mark.parametrize(
    "audio",
    (
        pytest.param(np.zeros((RATE, 2)), id="silence"),
        pytest.param(np.zeros((RATE // 10, 2)), id="shorter than one block"),
        pytest.param(tone(-90.0), id="below the absolute gate"),
    ),
)
def test_loudness_is_undefined_rather_than_a_number(audio):
    """A floor or an infinity would read as a real level to the learner."""
    assert integrated_lufs(audio, RATE) is None


def test_a_short_clip_is_still_measurable_above_the_block_length():
    assert integrated_lufs(tone(-23.0, seconds=0.5), RATE) == pytest.approx(-23.0, abs=0.05)


def test_malformed_input_is_rejected():
    with pytest.raises(ValueError):
        integrated_lufs(np.zeros((0, 2)), RATE)
    with pytest.raises(ValueError):
        integrated_lufs(np.zeros((RATE, 2, 2)), RATE)
    with pytest.raises(ValueError):
        integrated_lufs(np.zeros((RATE, 2)), 0)


def test_matching_gain_is_undefined_when_either_side_is():
    """An undefined measurement must leave preview audio untouched."""
    assert loudness_gain_db(-14.0, -20.0) == pytest.approx(6.0)
    assert loudness_gain_db(None, -20.0) is None
    assert loudness_gain_db(-14.0, None) is None
    assert loudness_gain_db(None, None) is None


def test_a_reading_is_dashed_when_there_is_none():
    assert format_lufs(-14.0) == "-14.0 LUFS"
    assert format_lufs(None) == "—"
