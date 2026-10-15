"""Dynamic bell response, detector mapping, stability and block determinism."""

from dataclasses import replace

import numpy as np
import pytest
from scipy import signal

from omazone.compressor import CompressorProcessor, CompressorSettings
from omazone.dynamic_eq import (
    DynamicEQProcessor,
    DynamicEQSettings,
    bandpass_sos,
    cut_db,
    dynamic_eq_parameters,
    render_dynamic_eq,
    response,
    settings_from_parameters,
    validate_settings,
)


def amplitude(audio, frequency, rate, start, end, channel=0):
    t = np.arange(start, end) / rate
    basis = np.column_stack((np.sin(2 * np.pi * frequency * t), np.cos(2 * np.pi * frequency * t)))
    return np.linalg.norm(np.linalg.lstsq(basis, audio[start:end, channel], rcond=None)[0])


def test_bursts_reduce_target_not_quiet_or_out_of_band_and_release_recovers():
    rate = 16000
    t = np.arange(rate * 3) / rate
    target = np.where((t >= 0.5) & (t < 1.5), 0.6, 0.005)
    audio = (target * np.sin(2 * np.pi * 3000 * t) + 0.06 * np.sin(2 * np.pi * 250 * t))[:, None]
    settings = DynamicEQSettings(3000, 4, -24, 9, 2, 80)
    result = render_dynamic_eq(audio, rate, settings)
    np.testing.assert_array_equal(result.audio[: int(rate * 0.45)], audio[: int(rate * 0.45)])
    target_gain = amplitude(result.audio, 3000, rate, rate, int(rate * 1.4)) / amplitude(
        audio, 3000, rate, rate, int(rate * 1.4)
    )
    other_gain = amplitude(result.audio, 250, rate, rate, int(rate * 1.4)) / amplitude(
        audio, 250, rate, rate, int(rate * 1.4)
    )
    assert 20 * np.log10(target_gain) == pytest.approx(-9, abs=0.02)
    assert 20 * np.log10(other_gain) == pytest.approx(0, abs=0.02)
    assert result.max_reduction_db == 9
    np.testing.assert_array_equal(result.audio[rate * 2 :], audio[rate * 2 :])
    assert result.reduction_db[-1] == 0


def test_detector_law_is_explicit_continuous_and_attenuation_only():
    settings = DynamicEQSettings(threshold_db=-24, max_cut_db=6)
    np.testing.assert_array_equal(
        cut_db([-60, -24, -23, -21, -18, 0], settings), [0, 0, 1, 3, 6, 6]
    )


def test_band_envelope_uses_the_same_linked_attack_release_definition_as_compression():
    audio = np.random.default_rng(8).normal(0, 0.1, (4096, 2))
    settings = DynamicEQSettings(attack_ms=3, release_ms=75)
    dynamic = DynamicEQProcessor()
    dynamic.prepare(16000, 2, len(audio))
    dynamic.set_settings(settings)
    dynamic.process_block(audio)
    compressor = CompressorProcessor()
    compressor.prepare(16000, 2, len(audio))
    compressor.set_settings(CompressorSettings(attack_ms=3, release_ms=75))
    compressor.process_block(signal.sosfilt(bandpass_sos(settings, 16000), audio, axis=0))
    np.testing.assert_array_equal(dynamic.last_detector_db, compressor.last_detector_db)


@pytest.mark.parametrize("rate", [8000, 16000, 44100, 48000, 96000])
@pytest.mark.parametrize("q", [0.5, 1, 20])
def test_static_bell_centre_gain_endpoints_and_poles(rate, q):
    frequency = min(2400, 0.45 * rate)
    settings = DynamicEQSettings(frequency=frequency, q=q, max_cut_db=12)
    gain = response(settings, rate, np.array([0, frequency, rate / 2]))
    np.testing.assert_allclose(np.abs(gain[[0, 2]]), 1, atol=1e-12)
    assert 20 * np.log10(abs(gain[1])) == pytest.approx(-12, abs=1e-10)
    sos = bandpass_sos(settings, rate)
    assert np.all(np.abs(np.roots(sos[0, 3:])) < 1)


def test_saturated_dynamic_cut_matches_fixed_bell_without_coefficient_updates():
    rate = 16000
    t = np.arange(rate) / rate
    audio = (0.7 * np.sin(2 * np.pi * 2400 * t))[:, None]
    settings = DynamicEQSettings(threshold_db=-40, attack_ms=0.1, max_cut_db=12)
    sos = bandpass_sos(settings, rate)
    static = sos.copy()
    static[0, :3] = sos[0, 3:] + (10 ** (-12 / 20) - 1) * sos[0, :3]
    expected = signal.sosfilt(static, audio, axis=0)
    rendered = render_dynamic_eq(audio, rate, settings)
    np.testing.assert_allclose(rendered.audio[rate // 2 :], expected[rate // 2 :], atol=1e-12)


@pytest.mark.parametrize("block_size", [1, 7, 113, 4096])
def test_block_equivalence_includes_diagnostics_and_reset(block_size):
    rate = 8000
    audio = np.random.default_rng(8).normal(0, 0.2, (4013, 2))
    settings = DynamicEQSettings(1000, 2, -30, 6, 1, 30)
    reference = render_dynamic_eq(audio, rate, settings, len(audio))
    actual = render_dynamic_eq(audio, rate, settings, block_size)
    np.testing.assert_array_equal(actual.audio, reference.audio)
    np.testing.assert_array_equal(actual.reduction_db, reference.reduction_db)
    np.testing.assert_array_equal(actual.detector_db, reference.detector_db)
    processor = DynamicEQProcessor()
    processor.prepare(rate, 2, len(audio))
    processor.set_settings(settings)
    first = processor.process_block(audio)
    processor.reset()
    np.testing.assert_array_equal(processor.process_block(audio), first)


def test_stereo_link_uses_louder_channel_and_preserves_proportions():
    rate = 16000
    t = np.arange(rate) / rate
    right = 0.8 * np.sin(2 * np.pi * 2400 * t)
    audio = np.column_stack((-0.02 * right, right))
    settings = DynamicEQSettings(threshold_db=-24, max_cut_db=9)
    result = render_dynamic_eq(audio, rate, settings)
    np.testing.assert_allclose(result.audio[:, 0], -0.02 * result.audio[:, 1], atol=1e-14)
    assert result.max_reduction_db == 9
    swapped = render_dynamic_eq(audio[:, ::-1], rate, settings)
    np.testing.assert_array_equal(swapped.audio[:, ::-1], result.audio)


@pytest.mark.parametrize("frequency", [20, 7200])
@pytest.mark.parametrize("q", [0.5, 20])
def test_rapid_gain_changes_near_frequency_limits_stay_finite(frequency, q):
    rate = 16000
    t = np.arange(rate) / rate
    burst = np.where((np.arange(rate) // 23) % 2, 1.0, 0.001)
    audio = (burst * np.sin(2 * np.pi * frequency * t))[:, None]
    settings = DynamicEQSettings(frequency, q, -40, 18, 0.1, 1)
    result = render_dynamic_eq(audio, rate, settings, 117)
    assert np.all(np.isfinite(result.audio)) and np.max(np.abs(result.audio)) < 2
    assert np.min(result.reduction_db) >= -18 and np.max(result.reduction_db) <= 0
    assert result.audio.shape == audio.shape


def test_zero_cut_is_exact_identity_and_silence_is_silent():
    audio = np.random.default_rng(83).normal(0, 0.1, (2000, 2))
    result = render_dynamic_eq(audio, 16000, DynamicEQSettings(max_cut_db=0))
    np.testing.assert_array_equal(result.audio, audio)
    result = render_dynamic_eq(np.zeros_like(audio), 16000, DynamicEQSettings())
    np.testing.assert_array_equal(result.audio, 0)


def test_recipe_roundtrip_retains_exact_values_and_rejects_unknown_data():
    settings = DynamicEQSettings(frequency=1234.567891, q=2.123456789)
    assert settings_from_parameters(dynamic_eq_parameters(settings)) == settings
    for value in (
        {"kind": "future"},
        {"kind": "dynamic-eq-v1"},
        {**dynamic_eq_parameters(settings), "unexpected": 1},
        [1],
        "bad",
    ):
        with pytest.raises(ValueError):
            settings_from_parameters(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("frequency", 19),
        ("frequency", 7201),
        ("q", 0.49),
        ("q", 21),
        ("threshold_db", 1),
        ("max_cut_db", -1),
        ("max_cut_db", 19),
        ("attack_ms", 0),
        ("release_ms", 0),
        ("q", True),
        ("frequency", np.nan),
        ("q", "1"),
    ],
)
def test_bad_settings_are_rejected(field, value):
    with pytest.raises(ValueError):
        validate_settings(replace(DynamicEQSettings(), **{field: value}), 16000)
