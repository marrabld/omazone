"""Frequency interpolation is visual only and respects measured support."""

from types import SimpleNamespace

import numpy as np

from omazone.reference_variation import analyse_variation
from omazone.reference_variation_view import density_image, display_envelope, smooth_display


def test_smooth_curve_preserves_band_values_without_overshooting():
    centres = np.array([1.0, 2.0, 3.0, 4.0])
    values = np.array([-30.0, -35.0, -32.0, -40.0])
    np.testing.assert_allclose(smooth_display(centres, values, centres), values)
    for index in range(3):
        x = np.linspace(centres[index], centres[index + 1], 101)
        result = smooth_display(centres, values, x)
        assert np.min(result) >= min(values[index : index + 2]) - 1e-12
        assert np.max(result) <= max(values[index : index + 2]) + 1e-12
    # Unlike straight segments, the slope is continuous at an interior knot.
    h = 1e-5
    y = smooth_display(centres, values, np.array([2 - h, 2.0, 2 + h]))
    assert abs((y[1] - y[0]) / h - (y[2] - y[1]) / h) < 0.001


def test_missing_bands_are_not_bridged_by_smoothing():
    centres = np.arange(5, dtype=float)
    x = np.linspace(0, 4, 81)
    result = smooth_display(centres, np.array([-30.0, -35.0, np.nan, -40.0, -42.0]), x)
    assert np.all(np.isnan(result[(x > 1) & (x < 3)]))
    assert np.all(np.isfinite(result[(x <= 1) | (x >= 3)]))
    assert np.all(np.isnan(smooth_display(centres, np.full(5, np.nan), x)))


def test_smoothed_envelope_preserves_order_and_measured_quantiles():
    variation = SimpleNamespace(
        edges=np.geomspace(100 / np.sqrt(2), 800 * np.sqrt(2), 5),
        frequency=np.array([100.0, 200.0, 400.0, 800.0]),
        valid=np.ones(4, dtype=bool),
        lower_db=np.array([-35.0, -50.0, -36.0, -60.0]),
        median_db=np.array([-30.0, -30.0, -35.0, -40.0]),
        upper_db=np.array([-29.0, -20.0, -20.0, -39.0]),
    )
    x = np.linspace(2, np.log10(800), 2001)
    lower, median, upper = display_envelope(variation, x)
    assert np.all(lower <= median) and np.all(median <= upper)
    measured = display_envelope(variation, np.log10(variation.frequency))
    for curve, expected in zip(
        measured, (variation.lower_db, variation.median_db, variation.upper_db), strict=True
    ):
        np.testing.assert_allclose(curve, expected)
    np.testing.assert_array_equal(variation.median_db, [-30.0, -30.0, -35.0, -40.0])


def test_isolated_supported_bands_draw_across_their_bandwidth_without_filling_gaps():
    rate = 48000
    t = np.arange(rate) / rate
    audio = np.concatenate(
        [
            0.1 * np.sin(2 * np.pi * 1000 * t)
            + (0.01 + second * 0.02) * np.sin(2 * np.pi * 8000 * t)
            for second in range(4)
        ]
    )[:, None]
    variation = analyse_variation(audio, rate)
    supported = np.flatnonzero(variation.valid)
    assert len(supported) == 2 and np.all(np.diff(supported) > 1)
    width = len(variation.valid) * 64
    edges = np.log10(variation.edges)
    x = edges[0] + (np.arange(width) + 0.5) * (edges[-1] - edges[0]) / width
    lower, median, upper = display_envelope(variation, x)
    alpha = density_image(variation)[..., 3]
    in_supported_band = np.zeros(width, dtype=bool)
    for band in supported:
        mask = (x >= edges[band]) & (x <= edges[band + 1])
        in_supported_band |= mask
        assert np.count_nonzero(mask) > 1
        assert np.max(alpha[mask]) > 0
        for curve, expected in zip(
            (lower, median, upper),
            (variation.lower_db, variation.median_db, variation.upper_db),
            strict=True,
        ):
            np.testing.assert_allclose(curve[mask], expected[band])
    assert np.all(np.isnan(median[~in_supported_band]))
    assert not np.any(alpha[~in_supported_band])


def test_single_constant_band_is_a_visible_line_with_no_fabricated_spread():
    edges = np.array([0.0, 1.0, 2.0, 3.0])
    centres = (edges[:-1] + edges[1:]) / 2
    x = np.linspace(0, 3, 61)
    result = smooth_display(centres, np.array([np.nan, -40.0, np.nan]), x, edges)
    inside = (x >= 1) & (x <= 2)
    np.testing.assert_array_equal(result[inside], -40.0)
    assert np.all(np.isnan(result[~inside]))
