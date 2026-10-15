"""Ceiling, release, delay compensation, stereo linking and independent peak checks."""

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from omazone.limiter import (
    LimiterProcessor,
    LimiterSettings,
    limiter_parameters,
    reconstructed_peak,
    render_limiter,
    settings_from_parameters,
    validate_settings,
)
from omazone.output_gain import OutputGainSettings, output_parameters
from omazone.pipeline import ChainRenderer
from omazone.project import AudioReference, Project, load_project, save_project


def independent_peak(audio):
    """A separately rendered 16x reference, not the limiter's chunked 8x guard."""
    reconstructed = signal.resample_poly(audio, 16, 1, axis=0, window=("kaiser", 14.0))
    return max(float(np.max(np.abs(audio))), float(np.max(np.abs(reconstructed))))


@pytest.mark.parametrize("rate", [16000, 44100, 48000, 96000])
def test_ceiling_on_impulses_intersample_peaks_bass_and_stereo(rate):
    time = np.arange(rate // 4) / rate
    left = 1.8 * np.sin(2 * np.pi * 60 * time)
    left[len(left) // 2] = 3
    audio = np.column_stack((left, -0.4 * left))
    settings = LimiterSettings(ceiling_db=-1, lookahead_ms=5, release_ms=100)
    original = audio.copy()
    result = render_limiter(audio, rate, settings)
    ceiling = 10 ** (settings.ceiling_db / 20)
    assert np.max(np.abs(result.audio)) <= ceiling * (1 + 1e-12)
    assert 20 * np.log10(independent_peak(result.audio)) <= settings.ceiling_db + 0.05
    np.testing.assert_allclose(result.audio[:, 1], -0.4 * result.audio[:, 0], atol=1e-14)
    assert result.max_reduction_db > 5
    assert result.reconstructed_peak_db <= settings.ceiling_db
    assert result.latency_samples == round(rate * 0.005)
    assert result.audio.shape == audio.shape
    np.testing.assert_array_equal(audio, original)


def test_inter_sample_overshoot_is_attenuated_and_the_guard_is_explicit():
    rate = 48000
    samples = np.arange(4800)
    audio = (0.95 * np.sin(2 * np.pi * 0.25 * samples + np.pi / 4))[:, None]
    assert np.max(np.abs(audio)) < 0.7
    assert independent_peak(audio) > 0.9
    settings = LimiterSettings(ceiling_db=-2)
    result = render_limiter(audio, rate, settings)
    assert result.guard_reduction_db > 1
    assert result.max_reduction_db == pytest.approx(result.guard_reduction_db)
    np.testing.assert_allclose(result.reduction_db, -result.guard_reduction_db)
    assert 20 * np.log10(independent_peak(result.audio)) <= -2 + 0.05


@pytest.mark.parametrize("size", [1, 7, 73, 4096])
def test_irregular_blocks_flush_and_aligned_diagnostics_match_one_block(size):
    rate = 1000
    audio = np.random.default_rng(245).normal(0, 0.7, (400, 2))
    audio[0, 0] = 3
    audio[-1, 1] = -2
    settings = LimiterSettings(lookahead_ms=7, release_ms=30)
    expected = render_limiter(audio, rate, settings, len(audio))
    result = render_limiter(audio, rate, settings, size)
    np.testing.assert_allclose(result.audio, expected.audio, atol=1e-12)
    np.testing.assert_allclose(result.reduction_db, expected.reduction_db, atol=1e-11)
    assert result.max_reduction_db == pytest.approx(expected.max_reduction_db, abs=1e-11)
    assert result.guard_reduction_db == pytest.approx(expected.guard_reduction_db, abs=1e-11)
    np.testing.assert_array_equal(result.time, np.arange(0, len(audio), 10) / rate)
    assert np.any(result.audio[0]) and np.any(result.audio[-1])


def test_lookahead_ramps_before_transient_and_release_recovers_without_makeup():
    rate = 1000
    audio = np.full((300, 2), 0.1)
    audio[100] = [3, 0.1]
    result = render_limiter(audio, rate, LimiterSettings(ceiling_db=-6, release_ms=30))
    gain = result.audio[:, 1] / 0.1
    assert gain[90] > gain[96] > gain[99] >= gain[100]
    assert gain[110] < gain[150] < gain[250] <= 1
    assert np.max(np.abs(result.audio)) < 10 ** (-6 / 20)
    for index, reduction in zip((result.time * rate).astype(int), result.reduction_db, strict=True):
        assert reduction == pytest.approx(20 * np.log10(gain[index]), abs=1e-10)


@pytest.mark.parametrize("frames", [2, 3, 1000])
def test_silence_and_safe_audio_are_sample_identical_including_short_files(frames):
    silence = np.zeros((frames, 2))
    silent = render_limiter(silence, 48000, LimiterSettings(), 7)
    np.testing.assert_array_equal(silent.audio, silence)
    assert silent.reconstructed_peak_db is None
    assert silent.max_reduction_db == silent.guard_reduction_db == 0
    audio = np.full((frames, 1), 0.05)
    result = render_limiter(audio, 48000, LimiterSettings(), 7)
    np.testing.assert_array_equal(result.audio, audio)
    assert result.max_reduction_db == 0


def test_core_reset_and_latency_are_explicit():
    processor = LimiterProcessor()
    processor.prepare(1000, 2, 40)
    processor.set_settings(LimiterSettings(lookahead_ms=10))
    assert processor.latency_samples == 10
    block = np.full((40, 2), [3, 0.2])
    first = processor.process_block(block)
    assert not np.any(first[:10])
    assert np.max(first[10:]) <= 10 ** (-1 / 20) * (1 + 1e-12)
    processor.process_block(block)
    processor.reset()
    np.testing.assert_array_equal(processor.process_block(block), first)
    with pytest.raises(ValueError, match="format"):
        processor.process_block(np.zeros((41, 2)))


def test_guard_chunk_boundaries_match_full_independent_reconstruction():
    audio = np.random.default_rng(345).normal(0, 0.1, (800, 2))
    audio[62:68] = [[0.8, 0], [-0.8, 0]] * 3
    whole = reconstructed_peak(audio, block_size=len(audio))
    for size in (1, 7, 64, 127):
        assert reconstructed_peak(audio, size) == pytest.approx(whole, abs=1e-12)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ceiling_db", 1),
        ("ceiling_db", -25),
        ("ceiling_db", True),
        ("lookahead_ms", 0),
        ("lookahead_ms", 21),
        ("release_ms", 9),
        ("release_ms", 2001),
        ("release_ms", np.nan),
    ],
)
def test_invalid_settings_and_saved_recipe(field, value):
    with pytest.raises(ValueError):
        validate_settings(LimiterSettings(**{field: value}))


def test_versioned_recipe_and_unknown_settings():
    settings = LimiterSettings(-2, 7, 80)
    assert settings_from_parameters(limiter_parameters(settings)) == settings
    assert settings_from_parameters({}) == LimiterSettings()
    for parameters in ({"kind": "future-limiter"}, {"kind": "limiter-v1", "ceiling_db": -1}):
        with pytest.raises(ValueError):
            settings_from_parameters(parameters)


def test_chain_limiter_follows_gain_reuses_prefixes_and_saved_bypass_keeps_choices(tmp_path):
    rate = 16000
    audio = np.random.default_rng(945).normal(0, 0.2, (rate, 2))
    audio[8000] = [2.5, -0.7]
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(source))
    project.stages["output"].bypassed = False
    project.stages["output"].parameters = output_parameters(OutputGainSettings(3))
    project.stages["limiter"].bypassed = False
    project.stages["limiter"].parameters = limiter_parameters(LimiterSettings(-2, 7, 80))
    renderer = ChainRenderer(audio, rate)
    first = renderer.render(project, block_size=73)
    np.testing.assert_array_equal(first.pre_output, audio)
    np.testing.assert_allclose(first.pre_limiter, audio * 10 ** (3 / 20))
    direct = render_limiter(first.pre_limiter, rate, LimiterSettings(-2, 7, 80), 73)
    np.testing.assert_array_equal(first.output, direct.audio)
    assert first.limiting is not None and first.limiting.audio is first.output
    assert id(first.pre_limiter) in first.loudness and id(first.pre_limiter) in first.true_peak

    project.stages["limiter"].parameters = limiter_parameters(LimiterSettings(-3, 7, 80))
    second = renderer.render(project)
    assert second.pre_limiter is first.pre_limiter
    assert second.pre_output is first.pre_output
    assert renderer.computations == {
        "repair": 1,
        "match": 1,
        "eq": 1,
        "filters": 1,
        "dynamic-eq": 1,
        "dynamics": 1,
        "output": 1,
        "limiter": 2,
    }
    saved = tmp_path / "limited.omazone.json"
    save_project(saved, project)
    reopened = load_project(saved)
    assert settings_from_parameters(reopened.stages["limiter"].parameters) == LimiterSettings(
        -3, 7, 80
    )
    np.testing.assert_allclose(ChainRenderer(audio, rate).render(reopened).output, second.output)
    reopened.stages["limiter"].bypassed = True
    bypass = renderer.render(reopened)
    assert bypass.limiting is None and bypass.output is second.pre_limiter
    assert reopened.stages["output"].parameters["gain_db"] == 3
    assert reopened.stages["limiter"].parameters["ceiling_db"] == -3
    reopened.stages["limiter"].bypassed = False
    assert renderer.render(reopened).limiting is second.limiting
    reopened.stages["output"].parameters = output_parameters(OutputGainSettings(-2))
    gain_change = renderer.render(reopened)
    assert gain_change.pre_output is first.pre_output
    assert renderer.computations["output"] == 2 and renderer.computations["limiter"] == 3


def test_old_project_migration_and_unknown_bypassed_limiter_are_safe(tmp_path):
    import json

    rate = 16000
    audio = np.random.default_rng(555).normal(0, 0.1, (rate, 1))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(source))
    saved = tmp_path / "old.omazone.json"
    save_project(saved, project)
    data = json.loads(saved.read_text())
    del data["stages"]["limiter"]
    saved.write_text(json.dumps(data))
    migrated = load_project(saved)
    assert migrated.stages["limiter"].bypassed
    np.testing.assert_array_equal(ChainRenderer(audio, rate).render(migrated).output, audio)
    migrated.stages["limiter"].parameters = {"kind": "future-limiter", "opaque": "keep"}
    save_project(saved, migrated)
    retained = load_project(saved)
    assert retained.stages["limiter"].parameters == migrated.stages["limiter"].parameters
    np.testing.assert_array_equal(ChainRenderer(audio, rate).render(retained).output, audio)
    retained.stages["limiter"].bypassed = False
    with pytest.raises(ValueError, match="Unsupported limiter"):
        ChainRenderer(audio, rate).render(retained)


@pytest.mark.parametrize("fixture", ["intersample", "bass", "impulses"])
def test_export_ceiling_against_independent_ffmpeg_true_peak_meter(tmp_path, fixture):
    import re
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("Independent FFmpeg meter is not installed")
    rate = 48000
    samples = np.arange(rate)
    # Sample peaks miss this waveform's reconstructed maximum.
    audio = (0.95 * np.sin(2 * np.pi * 0.25 * samples + np.pi / 4))[:, None]
    if fixture == "bass":
        audio = (2.5 * np.sin(2 * np.pi * 60 * samples / rate))[:, None]
    elif fixture == "impulses":
        audio = np.zeros((rate, 1))
        audio[0, 0], audio[rate // 2, 0], audio[-1, 0] = 3, -2, 2.5
    result = render_limiter(audio, rate, LimiterSettings(ceiling_db=-2))
    exported = tmp_path / "limited.wav"
    sf.write(exported, result.audio, rate, subtype="FLOAT")
    metered = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(exported),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    peak = re.search(r"True peak:\s+Peak:\s+(-?\d+(?:\.\d+)?)", metered.stderr)
    assert peak is not None, metered.stderr
    assert float(peak.group(1)) <= -2 + 0.1  # FFmpeg prints tenths of a dB.
