from dataclasses import replace

import numpy as np
import pytest
from scipy import signal

from omazone.filters import (
    SLOPES,
    FilterProcessor,
    FilterSettings,
    FiltersSettings,
    design_sos,
    frequency_response,
    render_filters,
)


def settings(**kwargs):
    return FiltersSettings((FilterSettings(**kwargs),))


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
@pytest.mark.parametrize("kind", ["highpass", "lowpass"])
@pytest.mark.parametrize("slope", SLOPES)
def test_butterworth_cutoff_endpoints_and_warped_analogue_response(rate, kind, slope):
    cutoff = 2000
    frequencies = np.array([0, 500, cutoff, 8000, rate / 2])
    sos = design_sos(settings(kind=kind, frequency=cutoff, slope=slope), rate)
    _, response = signal.sosfreqz(sos, worN=frequencies, fs=rate)
    magnitude = np.abs(response)
    assert magnitude[2] == pytest.approx(1 / np.sqrt(2), abs=1e-12)
    np.testing.assert_allclose(magnitude[[0, -1]], [0, 1] if kind == "highpass" else [1, 0], atol=1e-12)
    # Independent Butterworth magnitude formula after bilinear prewarping.
    ratio = np.tan(np.pi * frequencies[1:-1] / rate) / np.tan(np.pi * cutoff / rate)
    if kind == "highpass":
        ratio = 1 / ratio
    expected = 1 / np.sqrt(1 + ratio ** (2 * (slope // 6)))
    np.testing.assert_allclose(magnitude[1:-1], expected, atol=1e-12)


@pytest.mark.parametrize("kind", ["highpass", "lowpass"])
@pytest.mark.parametrize("slope", SLOPES)
def test_stopband_slope_in_prewarped_octaves(kind, slope):
    rate, cutoff = 48000, 1000
    ratios = np.array([0.01, 0.02]) if kind == "highpass" else np.array([50, 100])
    frequencies = rate / np.pi * np.arctan(ratios * np.tan(np.pi * cutoff / rate))
    _, response = signal.sosfreqz(
        design_sos(settings(kind=kind, frequency=cutoff, slope=slope), rate),
        worN=frequencies, fs=rate,
    )
    difference = np.diff(20 * np.log10(np.abs(response)))[0]
    expected = 20 * np.log10(2) * (slope // 6) * (1 if kind == "highpass" else -1)
    assert difference == pytest.approx(expected, abs=0.003)


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
@pytest.mark.parametrize("frequency,q", [(50, 30), (1000, 1), (12000, 20)])
def test_notch_null_endpoints_and_minus_three_db_bandwidth(rate, frequency, q):
    sos = design_sos(settings(kind="notch", frequency=frequency, q=q), rate)
    _, response = signal.sosfreqz(sos, worN=[0, frequency, rate / 2], fs=rate)
    np.testing.assert_allclose(np.abs(response)[[0, -1]], 1, atol=1e-10)
    assert abs(response[1]) < 1e-7
    # Locate half-power crossings without assuming symmetric digital edges.
    from scipy.optimize import brentq

    def half_power(f):
        _, value = signal.sosfreqz(sos, worN=[f], fs=rate)
        return abs(value[0]) ** 2 - 0.5

    lower = brentq(half_power, 0, frequency)
    upper = brentq(half_power, frequency, rate / 2)
    assert upper - lower == pytest.approx(frequency / q, rel=1e-8)


@pytest.mark.parametrize("rate", [16000, 44100, 48000, 96000, 192000])
@pytest.mark.parametrize("kind", ["highpass", "lowpass", "notch"])
@pytest.mark.parametrize("edge", ["low", "high"])
def test_extreme_parameters_have_stable_poles_and_finite_output(rate, kind, edge):
    frequency = 20 if edge == "low" else rate / 2 - 0.01
    recipe = settings(kind=kind, frequency=frequency, slope=24, q=100)
    sos = design_sos(recipe, rate)
    assert all(np.all(np.abs(np.roots(section[3:])) < 1) for section in sos)
    impulse = np.zeros((4097, 2))
    impulse[0, 0] = 1
    impulse[0, 1] = -0.4
    output = render_filters(impulse, rate, recipe, block_size=31)
    assert np.all(np.isfinite(output))
    assert np.max(np.abs(output)) <= 1.01
    np.testing.assert_allclose(output[:, 1], -0.4 * output[:, 0], atol=1e-12)


def test_irregular_blocks_reset_coefficient_changes_and_channel_isolation():
    rate = 48000
    audio = np.random.default_rng(601).normal(0, 0.1, (5003, 2))
    audio[:, 1] = 0
    recipe = FiltersSettings((
        FilterSettings(frequency=40, slope=24),
        FilterSettings(kind="notch", frequency=1000, q=20),
        FilterSettings(kind="lowpass", frequency=12000, slope=18),
    ))
    expected = signal.sosfilt(design_sos(recipe, rate), audio, axis=0)
    processor = FilterProcessor()
    processor.prepare(rate, 2, 513)
    processor.set_settings(recipe)
    blocks, start = [], 0
    sizes = (1, 17, 513, 2, 127)
    while start < len(audio):
        count = sizes[len(blocks) % len(sizes)]
        blocks.append(processor.process_block(audio[start : start + count]))
        start += count
    np.testing.assert_array_equal(np.concatenate(blocks), expected)
    np.testing.assert_array_equal(expected[:, 1], 0)
    processor.reset()
    np.testing.assert_array_equal(processor.process_block(audio[:100]), expected[:100])
    updated = settings(kind="lowpass", frequency=2000)
    processor.set_settings(updated)
    np.testing.assert_array_equal(
        processor.process_block(audio[:100]),
        signal.sosfilt(design_sos(updated, rate), audio[:100], axis=0),
    )
    assert processor.latency_samples == 0


@pytest.mark.parametrize("size", [1, 17, 4096])
@pytest.mark.parametrize("channels", [1, 2])
def test_render_preserves_length_input_and_block_equivalence(size, channels):
    audio = np.random.default_rng(602).normal(0, 0.05, (1001, channels))
    original = audio.copy()
    recipe = settings(kind="notch", frequency=50, q=50)
    expected = signal.sosfilt(design_sos(recipe, 48000), audio, axis=0)
    output = render_filters(audio, 48000, recipe, block_size=size)
    assert output.shape == audio.shape
    np.testing.assert_array_equal(output, expected)
    np.testing.assert_array_equal(audio, original)


def test_empty_and_individually_bypassed_filters_are_exact_and_plot_is_neutral():
    audio = np.random.default_rng(603).normal(size=(101, 2))
    for recipe in (FiltersSettings(), settings(enabled=False)):
        output = render_filters(audio, 48000, recipe)
        np.testing.assert_array_equal(output, audio)
        assert not np.shares_memory(output, audio)
        assert design_sos(recipe, 48000).shape == (0, 6)
        frequency, db = frequency_response(recipe, 48000)
        np.testing.assert_array_equal(db, 0)
        assert frequency[0] == 0 and frequency[-1] < 24000
    active = FilterSettings(kind="lowpass", frequency=4000)
    mixed = FiltersSettings((FilterSettings(enabled=False), active))
    np.testing.assert_array_equal(design_sos(mixed, 48000), design_sos(FiltersSettings((active,)), 48000))


def test_cascade_response_is_sum_of_individual_responses():
    filters = (FilterSettings(), FilterSettings(kind="lowpass", frequency=12000))
    frequency, combined = frequency_response(FiltersSettings(filters), 48000)
    individual = [frequency_response(FiltersSettings((item,)), 48000)[1] for item in filters]
    np.testing.assert_allclose(combined[1:], np.sum(individual, axis=0)[1:], atol=1e-10)
    assert np.all(np.isfinite(combined))
    assert len(frequency) == len(combined)


def test_two_sample_render_and_impulse_tail_are_not_padded_or_phase_compensated():
    impulse = np.zeros((2, 1))
    impulse[0] = 1
    recipe = settings(kind="lowpass", frequency=500)
    output = render_filters(impulse, 48000, recipe, block_size=1)
    assert output.shape == impulse.shape
    assert 0 < output[0, 0] < output[1, 0]


@pytest.mark.parametrize("kind,frequency,q,seconds", [
    ("highpass", 20, 1, 2), ("lowpass", 20, 1, 2), ("notch", 50, 100, 20),
])
def test_impulse_settles_but_high_q_low_frequency_notch_can_ring(kind, frequency, q, seconds):
    rate = 48000
    impulse = np.zeros((rate * seconds, 1))
    impulse[0] = 1
    output = render_filters(impulse, rate, settings(kind=kind, frequency=frequency, q=q, slope=24))
    assert np.max(np.abs(output[-rate // 10 :])) < 1e-10
    if kind == "notch":
        assert np.max(np.abs(output[rate : rate + rate // 10])) > 1e-6


@pytest.mark.parametrize("changes", [
    {"kind": "bell"}, {"frequency": 19}, {"frequency": 24000},
    {"frequency": float("nan")}, {"frequency": float("inf")}, {"frequency": True},
    {"frequency": 50 + 1j}, {"q": 0}, {"q": 101}, {"q": float("nan")},
    {"q": False}, {"slope": 8}, {"slope": 12.0}, {"slope": True},
    {"enabled": 1}, {"kind": "notch", "frequency": 12000, "q": 0.1},
])
def test_invalid_settings_including_disabled_filters_are_rejected(changes):
    item = replace(FilterSettings(), **changes)
    if "enabled" in changes:
        with pytest.raises(ValueError):
            design_sos(FiltersSettings((item,)), 48000)
        return
    for enabled in (True, False):
        with pytest.raises(ValueError):
            design_sos(FiltersSettings((replace(item, enabled=enabled),)), 48000)


def test_invalid_count_collection_and_block_format_do_not_enter_filter():
    for recipe in (FiltersSettings((FilterSettings(),) * 7), FiltersSettings([FilterSettings()]), None):
        with pytest.raises(ValueError):
            design_sos(recipe, 48000)
    processor = FilterProcessor()
    for args in ((0, 2, 16), (48000.0, 2, 16), (48000, True, 16), (48000, 2, 0)):
        with pytest.raises(ValueError):
            processor.prepare(*args)
    processor.prepare(48000, 2, 16)
    for block in (np.zeros(2), np.zeros((2, 1)), np.zeros((0, 2)), np.zeros((17, 2)), np.full((2, 2), np.nan)):
        with pytest.raises(ValueError):
            processor.process_block(block)
    for size in (0, -1, True, 4.0):
        with pytest.raises(ValueError):
            render_filters(np.zeros((2, 2)), 48000, FiltersSettings(), size)
