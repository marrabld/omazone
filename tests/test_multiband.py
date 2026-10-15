"""Complementary crossover reconstruction, timing and independent band dynamics."""

from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from omazone.compressor import CompressorSettings
from omazone.dynamics import DynamicsSettings, dynamics_parameters, settings_from_parameters
from omazone.multiband import (
    BandSettings,
    CrossoverProcessor,
    MultibandSettings,
    crossover_filters,
    default_settings,
    multiband_parameters,
    render_multiband,
    validate_settings,
)
from omazone.multiband import settings_from_parameters as multiband_settings
from omazone.pipeline import ChainRenderer
from omazone.project import AudioReference, Project, load_project, save_project


def neutral_settings(rate=16000, taps=129):
    return replace(default_settings(rate), taps=taps, bands=tuple(
        BandSettings(CompressorSettings(ratio=1)) for _ in range(3)
    ))


@pytest.mark.parametrize("rate", [16000, 44100, 48000, 96000])
def test_complex_frequency_responses_sum_to_unit_magnitude_and_common_delay(rate):
    settings = neutral_settings(rate, taps=2049)
    filters = crossover_filters(settings, rate)
    omega = np.linspace(0, np.pi, 1201)
    responses = [signal.freqz(coefficients, worN=omega)[1] for coefficients in filters]
    expected = np.exp(-1j * omega * 1024)
    np.testing.assert_allclose(sum(responses), expected, atol=1e-10)
    np.testing.assert_allclose(np.abs(sum(responses)), 1, atol=1e-10)
    for index, frequency in enumerate((settings.low_hz, settings.high_hz)):
        values = [signal.freqz(coefficients, worN=[2 * np.pi * frequency / rate])[1][0] for coefficients in filters]
        assert abs(values[index]) == pytest.approx(0.5, abs=0.001)
        assert abs(values[index + 1]) == pytest.approx(0.5, abs=0.001)


@pytest.mark.parametrize("size", [1, 7, 73, 4096])
def test_neutral_bands_reconstruct_noise_and_endpoint_impulses_across_blocks(size):
    rate = 1000
    settings = neutral_settings(rate)
    audio = np.random.default_rng(468).normal(0, 0.1, (400, 2))
    audio[0] = [0.8, -0.4]
    audio[-1] = [-0.7, 0.3]
    original = audio.copy()
    result = render_multiband(audio, rate, settings, size)
    np.testing.assert_allclose(result.audio, audio, atol=1e-12)
    np.testing.assert_array_equal(audio, original)
    assert result.latency_samples == 64
    assert result.audio.shape == audio.shape
    np.testing.assert_array_equal(result.time, np.arange(0, 400, 10) / rate)
    np.testing.assert_array_equal(result.reduction_db, np.zeros((40, 3)))
    assert result.band_max_reduction_db == (0, 0, 0)


def test_bypass_preserves_audio_and_compressor_choices_not_muting_the_band():
    settings = replace(default_settings(), bands=tuple(
        BandSettings(CompressorSettings(threshold_db=-40, ratio=6, makeup_db=12), bypassed=True)
        for _ in range(3)
    ))
    audio = np.random.default_rng(881).normal(0, 0.1, (2400, 1))
    result = render_multiband(audio, 48000, settings, 67)
    np.testing.assert_allclose(result.audio, audio, atol=1e-12)
    assert result.max_reduction_db == 0
    assert settings.bands[0].compressor.makeup_db == 12


def test_only_low_band_compresses_and_all_channels_share_its_envelope():
    rate = 16000
    time = np.arange(rate) / rate
    left = 0.5 * np.sin(2 * np.pi * 60 * time) + 0.1 * np.sin(2 * np.pi * 800 * time)
    left += 0.05 * np.sin(2 * np.pi * 5000 * time)
    audio = np.column_stack((left, -0.4 * left))
    neutral = neutral_settings(rate, 513)
    settings = replace(neutral, bands=(
        BandSettings(CompressorSettings(threshold_db=-30, ratio=4)),
        neutral.bands[1], neutral.bands[2],
    ))
    baseline = render_multiband(audio, rate, neutral)
    result = render_multiband(audio, rate, settings, 73)
    assert result.band_max_reduction_db[0] > 8
    assert result.band_max_reduction_db[1:] == (0, 0)
    np.testing.assert_allclose(result.solo_audio[1], baseline.solo_audio[1], atol=1e-7)
    np.testing.assert_allclose(result.solo_audio[2], baseline.solo_audio[2], atol=1e-7)
    np.testing.assert_allclose(result.audio[:, 1], -0.4 * result.audio[:, 0], atol=1e-13)
    assert np.any(np.abs(result.audio - audio) > 0.1)
    expected = render_multiband(audio, rate, settings, len(audio))
    np.testing.assert_allclose(result.audio, expected.audio, atol=1e-12)
    np.testing.assert_allclose(result.detector_db, expected.detector_db, atol=1e-8)
    np.testing.assert_allclose(result.reduction_db, expected.reduction_db, atol=1e-8)


def test_solo_headroom_is_audition_only_and_does_not_change_the_mix():
    rate = 16000
    time = np.arange(4000) / rate
    audio = (2 * np.sin(2 * np.pi * 60 * time))[:, None]
    settings = neutral_settings(rate, taps=513)
    result = render_multiband(audio, rate, settings)
    np.testing.assert_allclose(result.audio, audio, atol=1e-12)
    assert result.solo_audio[0].dtype == np.float32
    assert np.max(np.abs(result.solo_audio[0])) > 1
    assert np.max(np.abs(result.solo_playback[0])) <= 0.950001
    assert result.solo_playback[0] is not result.solo_audio[0]


def test_reset_impulse_latency_and_frequency_edit_are_offline():
    processor = CrossoverProcessor()
    processor.prepare(16000, 2, 200)
    settings = neutral_settings(taps=129)
    processor.set_settings(settings)
    impulse = np.zeros((200, 2))
    impulse[0] = [1, -0.5]
    bands = processor.process_block(impulse)
    expected = np.zeros_like(impulse)
    expected[64] = [1, -0.5]
    np.testing.assert_allclose(sum(bands), expected, atol=1e-14)
    processor.process_block(impulse)
    processor.reset()
    repeated = processor.process_block(impulse)
    for first, second in zip(bands, repeated, strict=True):
        np.testing.assert_array_equal(first, second)
    processor.set_settings(replace(settings, low_hz=400, high_hz=3000))
    changed = processor.process_block(impulse)
    np.testing.assert_allclose(sum(changed), expected, atol=1e-14)
    assert np.any(changed[0] != bands[0])
    with pytest.raises(ValueError, match="format"):
        processor.process_block(np.zeros((201, 2)))


def test_silence_and_files_shorter_than_crossover_delay():
    settings = neutral_settings(taps=129)
    for audio in (np.zeros((3, 2)), np.array([[0.5, -0.2], [-0.3, 0.1]])):
        result = render_multiband(audio, 16000, settings, 7)
        np.testing.assert_allclose(result.audio, audio, atol=1e-12)
        assert np.all(np.isfinite(result.detector_db))


@pytest.mark.parametrize("changes", [
    {"low_hz": 0}, {"high_hz": 100}, {"low_hz": np.nan}, {"high_hz": 8000},
    {"low_hz": True}, {"taps": 128}, {"taps": 8195}, {"bands": ()},
])
def test_invalid_crossovers_and_band_structure_are_rejected(changes):
    with pytest.raises(ValueError):
        validate_settings(replace(MultibandSettings(), **changes), 16000)


def test_versioned_mode_selection_retains_both_recipes_and_legacy_defaults():
    multiband = replace(neutral_settings(), low_hz=300)
    settings = DynamicsSettings("three-band", CompressorSettings(threshold_db=-22), multiband)
    parameters = dynamics_parameters(settings)
    assert parameters["kind"] == "dynamics-v2"
    assert settings_from_parameters(parameters, 16000) == settings
    broadband = replace(settings, mode="broadband")
    assert settings_from_parameters(dynamics_parameters(broadband), 16000) == broadband
    assert settings_from_parameters({}).mode == "broadband"
    assert multiband_settings(multiband_parameters(multiband)) == multiband
    parameters["multiband"]["bands"].pop()
    with pytest.raises(ValueError):
        settings_from_parameters(parameters)


def test_saved_chain_crossover_changes_reuse_earlier_prefixes_and_preserve_bypass(tmp_path):
    rate = 1000
    audio = np.random.default_rng(341).normal(0, 0.1, (500, 2))
    source = tmp_path / "original.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(source))
    settings = DynamicsSettings("three-band", CompressorSettings(threshold_db=-22), neutral_settings(rate))
    project.stages["dynamics"].bypassed = False
    project.stages["dynamics"].parameters = dynamics_parameters(settings)
    renderer = ChainRenderer(audio, rate)
    first = renderer.render(project, block_size=73)
    np.testing.assert_allclose(first.output, audio, atol=1e-12)
    edited = replace(settings, multiband=replace(settings.multiband, low_hz=120))
    project.stages["dynamics"].parameters = dynamics_parameters(edited)
    second = renderer.render(project)
    assert second.matched is first.matched and second.equalized is first.equalized
    assert renderer.computations == {"repair": 1, "match": 1, "eq": 1, "filters": 1, "dynamic-eq": 1, "dynamics": 2, "output": 2}
    saved = tmp_path / "three-band.omazone.json"
    save_project(saved, project)
    reopened = load_project(saved)
    assert settings_from_parameters(reopened.stages["dynamics"].parameters, rate) == edited
    np.testing.assert_allclose(ChainRenderer(audio, rate).render(reopened).output, second.output, atol=1e-12)
    reopened.stages["dynamics"].bypassed = True
    bypass = renderer.render(reopened)
    assert bypass.output is first.equalized
    assert reopened.stages["dynamics"].parameters["multiband"]["low_hz"] == 120
    assert reopened.stages["dynamics"].parameters["broadband"]["threshold_db"] == -22
    reopened.stages["dynamics"].parameters = {"kind": "future-band-mode", "opaque": "keep"}
    assert renderer.render(reopened).output is first.equalized
    reopened.stages["dynamics"].bypassed = False
    with pytest.raises(ValueError, match="Unsupported dynamics"):
        renderer.render(reopened)


def test_legacy_broadband_remains_usable_when_sample_rate_cannot_support_crossovers():
    from omazone.dynamics import render_dynamics

    settings = settings_from_parameters({}, rate=10)
    audio = np.ones((10, 1)) * 0.05
    np.testing.assert_array_equal(render_dynamics(audio, 10, settings).audio, audio)
