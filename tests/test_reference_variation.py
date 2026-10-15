"""Measured reference ranges: empirical variation, not confidence or random EQ."""

import copy

import numpy as np
import pytest
from scipy import signal

from omazone.engine import Spectrum
from omazone.reference_variation import (
    DB_EDGES,
    analyse_variation,
    band_density,
    band_edges,
    parse_variation,
    variation_dict,
)
from omazone.reference_variation_view import density_image

RATE = 16000


def excerpt_pair():
    first = np.random.default_rng(98).normal(0, 0.1, (RATE, 2))
    second = signal.sosfilt(signal.butter(2, 600, fs=RATE, output="sos"), first, axis=0)
    return first, second


def test_bimodal_distribution_has_empirical_quantiles_and_two_density_peaks():
    first, second = excerpt_pair()
    a = analyse_variation(np.tile(first, (4, 1)), RATE)
    b = analyse_variation(np.tile(second, (4, 1)), RATE)
    combined = analyse_variation(np.vstack((np.tile(first, (4, 1)), np.tile(second, (4, 1)))), RATE)
    valid = a.valid & b.valid & combined.valid
    np.testing.assert_allclose(
        combined.lower_db[valid], np.minimum(a.median_db, b.median_db)[valid], atol=1e-10
    )
    np.testing.assert_allclose(
        combined.upper_db[valid], np.maximum(a.median_db, b.median_db)[valid], atol=1e-10
    )
    np.testing.assert_allclose(
        combined.median_db[valid], ((a.median_db + b.median_db) / 2)[valid], atol=1e-10
    )
    band = np.argmax(np.where(valid, np.abs(a.median_db - b.median_db), -1))
    occupied = np.flatnonzero(combined.counts[band])
    assert len(occupied) == 2
    np.testing.assert_array_equal(combined.counts[band, occupied], [4, 4])
    assert combined.counts[band, (occupied[0] + occupied[1]) // 2] == 0
    assert combined.accepted_windows == combined.sampled_windows == 8


def test_level_scaling_and_excerpt_order_do_not_change_the_profile():
    first, second = excerpt_pair()
    audio = np.vstack((np.tile(first, (4, 1)), np.tile(second, (4, 1))))
    original = audio.copy()
    before = analyse_variation(audio, RATE)
    for transformed in (
        audio * 0.25,
        audio * 4,
        np.vstack((np.tile(second, (4, 1)), np.tile(first, (4, 1)))),
    ):
        result = analyse_variation(transformed, RATE)
        np.testing.assert_allclose(result.median_db, before.median_db, atol=1e-10)
        np.testing.assert_array_equal(result.counts, before.counts)
        np.testing.assert_array_equal(result.valid, before.valid)
    np.testing.assert_array_equal(audio, original)


def test_duplicate_excerpts_preserve_range_and_probability_not_claimed_reference_count():
    first, second = excerpt_pair()
    audio = np.vstack((np.tile(first, (4, 1)), np.tile(second, (4, 1))))
    before = analyse_variation(audio, RATE)
    after = analyse_variation(np.tile(audio, (2, 1)), RATE)
    np.testing.assert_array_equal(after.counts, before.counts * 2)
    np.testing.assert_allclose(after.lower_db, before.lower_db)
    np.testing.assert_allclose(after.upper_db, before.upper_db)
    np.testing.assert_array_equal(density_image(before), density_image(after))


def test_duplicate_unique_excerpts_keep_density_but_follow_documented_quantile_convention():
    first, _ = excerpt_pair()
    audio = np.vstack(
        [
            signal.sosfilt(signal.butter(2, cutoff, fs=RATE, output="sos"), first, axis=0)
            for cutoff in (300, 600, 1500, 5000)
        ]
    )
    before = analyse_variation(audio, RATE)
    after = analyse_variation(np.tile(audio, (2, 1)), RATE)
    np.testing.assert_array_equal(after.counts, 2 * before.counts)
    valid = before.valid & after.valid
    assert np.any(np.abs(before.lower_db[valid] - after.lower_db[valid]) > 0.01)


def test_silence_and_sparse_targets_do_not_create_an_envelope():
    assert analyse_variation(np.ones((RATE * 3, 1)), RATE) is None
    silent = analyse_variation(np.zeros((RATE * 8, 2)), RATE)
    assert not silent.available and silent.accepted_windows == 0
    assert not np.any(silent.counts)
    first, _ = excerpt_pair()
    mostly_silent = analyse_variation(np.vstack((first, np.zeros((RATE * 7, 2)))), RATE)
    assert mostly_silent.accepted_windows == 1 and not mostly_silent.available
    np.testing.assert_array_equal(density_image(mostly_silent)[..., 3], 0)


def test_silent_and_very_quiet_excerpts_are_gated_before_normalising():
    first, _ = excerpt_pair()
    audio = np.vstack((np.tile(first, (4, 1)), first * 1e-4, np.zeros_like(first)))
    result = analyse_variation(audio, RATE)
    assert result.sampled_windows == 6 and result.accepted_windows == 4
    reference = analyse_variation(np.tile(first, (4, 1)), RATE)
    np.testing.assert_array_equal(result.counts, reference.counts)


def test_stereo_power_is_averaged_without_phase_cancellation():
    mono = np.random.default_rng(99).normal(0, 0.1, (RATE * 4, 1))
    stereo = np.column_stack((mono[:, 0], -mono[:, 0]))
    a, b = analyse_variation(mono, RATE), analyse_variation(stereo, RATE)
    np.testing.assert_allclose(a.median_db, b.median_db, atol=1e-12)
    np.testing.assert_array_equal(a.counts, b.counts)


def test_fractional_bin_integration_handles_narrow_bands_and_unsupported_highs():
    edges = band_edges(48000)
    for count in (2001, 4001):
        spectrum = Spectrum(np.linspace(0, 8000, count), np.ones(count))
        values = band_density(spectrum, edges)
        supported = edges[1:] <= 8000
        np.testing.assert_allclose(values[supported], 10 * np.log10(1 / 8000), atol=1e-10)
        assert np.all(np.isnan(values[~supported]))


def test_under_resolved_frequency_bands_have_no_quantile_or_density_display():
    rate = 192000
    audio = np.random.default_rng(100).normal(0, 0.1, (rate * 4, 1))
    result = analyse_variation(audio, rate)
    assert not result.valid[0] and result.band_counts[0] == 4
    assert result.lower_db[0] == result.median_db[0] == result.upper_db[0] == 0
    np.testing.assert_array_equal(density_image(result)[0, :, 3], 0)


def test_partial_final_excerpt_is_not_reweighted_as_a_full_observation():
    first, _ = excerpt_pair()
    a = analyse_variation(np.tile(first, (4, 1)), RATE)
    b = analyse_variation(np.vstack((np.tile(first, (4, 1)), first[: RATE // 2] * 10)), RATE)
    assert b.sampled_windows == 4
    np.testing.assert_array_equal(a.counts, b.counts)


def test_long_reference_analysis_caps_observations_deterministically():
    rate = 100
    audio = np.random.default_rng(103).normal(0, 0.1, (rate * 4100, 1))
    result = analyse_variation(audio, rate)
    assert result.frames == rate * 4100
    assert result.sampled_windows == 4096
    assert result.accepted_windows == 4096
    assert result.counts.size <= 29 * 120


def test_44100_hz_fft_rounding_does_not_drop_the_last_eighth_of_each_excerpt():
    rate = 44100
    audio = np.zeros((rate * 4, 1))
    rng = np.random.default_rng(104)
    for index in range(4):
        audio[(index + 1) * rate - rate // 10 : (index + 1) * rate] = rng.normal(
            0, 0.1, (rate // 10, 1)
        )
    result = analyse_variation(audio, rate)
    assert result.accepted_windows == 4 and result.available


def test_profile_is_reproducible_roundtrips_and_has_no_audio_or_gaussian_model():
    first, second = excerpt_pair()
    audio = np.vstack((np.tile(first, (4, 1)), np.tile(second, (4, 1))))
    data = variation_dict(analyse_variation(audio, RATE))
    assert variation_dict(parse_variation(data)) == data
    assert variation_dict(analyse_variation(audio, RATE)) == data
    assert "audio" not in data and "sigma" not in data and "variance" not in data


@pytest.mark.parametrize(
    "change",
    [
        "kind",
        "policy",
        "boolean_policy",
        "fractional_count",
        "boolean_count",
        "negative_count",
        "count_total",
        "range_order",
        "mask",
        "frames",
        "oversized",
    ],
)
def test_invalid_saved_distributions_are_rejected_not_repaired(change):
    first, _ = excerpt_pair()
    data = copy.deepcopy(variation_dict(analyse_variation(np.tile(first, (4, 1)), RATE)))
    if change == "kind":
        data["kind"] = "future"
    elif change == "policy":
        data["policy"]["weighting"] = "unknown"
    elif change == "boolean_policy":
        data["policy"]["excerpt_seconds"] = True
    elif change == "fractional_count":
        data["counts"][0][0] = 0.5
    elif change == "boolean_count":
        data["counts"][0][0] = True
    elif change == "negative_count":
        data["counts"][0][0] = -1
    elif change == "count_total":
        data["band_counts"][0] = 999
    elif change == "range_order":
        data["lower_db"][0] = 0
    elif change == "mask":
        data["valid"][0] = not data["valid"][0]
    elif change == "frames":
        data["frames"] = 1
    else:
        data["edges"] = list(range(200))
    with pytest.raises(ValueError):
        parse_variation(data)


def test_zero_width_observed_range_is_a_line_not_a_fabricated_density_band():
    first, _ = excerpt_pair()
    result = analyse_variation(np.tile(first, (4, 1)), RATE)
    np.testing.assert_array_equal(density_image(result)[..., 3], 0)
    assert len(DB_EDGES) == 121


def test_density_supersampling_preserves_empty_histogram_bins():
    from types import SimpleNamespace

    counts = np.zeros((2, 120), dtype=int)
    counts[:, 60] = 4
    counts[:, 70] = 4
    variation = SimpleNamespace(
        valid=np.ones(2, dtype=bool),
        edges=np.array([100, 200, 400]),
        frequency=np.sqrt(np.array([100 * 200, 200 * 400])),
        counts=counts,
        band_counts=np.array([8, 8]),
        lower_db=np.array([-65.0, -65.0]),
        median_db=np.array([-55.0, -55.0]),
        upper_db=np.array([-45.0, -45.0]),
    )
    image = density_image(variation)
    assert image.shape == (128, 960, 4)
    # Each measured 1 dB bin is repeated, not blurred into neighbouring bins.
    assert np.all(image[:, 60 * 8 : 61 * 8, 3] > 0)
    assert np.all(image[:, 70 * 8 : 71 * 8, 3] > 0)
    assert not np.any(image[:, 61 * 8 : 70 * 8, 3])
