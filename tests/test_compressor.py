"""Compressor gain law, stereo envelope, block state and saved-chain replay."""

import numpy as np
import pytest
import soundfile as sf

from omazone.compressor import (
    CompressorProcessor,
    CompressorSettings,
    compressor_parameters,
    gain_reduction_db,
    render_compressor,
    settings_from_parameters,
    validate_settings,
)
from omazone.manual_eq import BellSettings, EQSettings, eq_parameters, render_eq
from omazone.pipeline import ChainRenderer
from omazone.project import AudioReference, Project, load_project, save_project


def test_static_knee_continuity_and_hard_knee():
    hard = CompressorSettings(threshold_db=-18, ratio=4, knee_db=0)
    np.testing.assert_allclose(
        gain_reduction_db(np.array([-30, -18, -6, 0]), hard), [0, 0, -9, -13.5]
    )
    soft = CompressorSettings(threshold_db=-18, ratio=4, knee_db=8)
    for edge in (-22, -14):
        around = gain_reduction_db(np.array([edge - 1e-5, edge, edge + 1e-5]), soft)
        assert max(np.diff(around)) < 1e-4
    assert gain_reduction_db(-18, soft) == pytest.approx(-0.75)
    assert gain_reduction_db(0, CompressorSettings(ratio=1)) == 0


@pytest.mark.parametrize("detector", ["peak", "rms"])
def test_stateful_blocks_and_stereo_linking(detector):
    rate = 16000
    t = np.arange(rate) / rate
    left = (0.1 + 0.65 * (t > 0.2)) * np.sin(2 * np.pi * 440 * t)
    audio = np.column_stack((left, -left * 0.4))
    settings = CompressorSettings(
        threshold_db=-30, ratio=4, attack_ms=5, release_ms=120, knee_db=0, detector=detector
    )
    expected = render_compressor(audio, rate, settings, block_size=len(audio))
    for size in (1, 17, 509, 4096):
        result = render_compressor(audio, rate, settings, block_size=size)
        np.testing.assert_allclose(result.audio, expected.audio, atol=1e-13)
        np.testing.assert_allclose(result.detector_db, expected.detector_db, atol=1e-13)
        np.testing.assert_allclose(result.reduction_db, expected.reduction_db, atol=1e-13)
    np.testing.assert_allclose(expected.audio[:, 1], -0.4 * expected.audio[:, 0], atol=1e-14)
    assert np.min(expected.reduction_db) < -5
    assert expected.max_reduction_db >= -float(np.min(expected.reduction_db))
    assert render_compressor(audio, rate, settings, 17).max_reduction_db == pytest.approx(
        expected.max_reduction_db
    )
    processor = CompressorProcessor()
    processor.prepare(rate, 2, 160)
    processor.set_settings(settings)
    first = processor.process_block(audio[3200:3360])
    processor.process_block(audio[3360:3520])
    processor.reset()
    np.testing.assert_array_equal(processor.process_block(audio[3200:3360]), first)


def test_attack_release_peak_rms_and_makeup_not_a_limiter():
    rate = 1000
    audio = np.zeros((1500, 2))
    audio[200:700, 0] = 0.9
    audio[200:700, 1] = 0.2
    settings = CompressorSettings(
        threshold_db=-18, ratio=4, knee_db=0, attack_ms=30, release_ms=100
    )
    peak = render_compressor(audio, rate, settings, block_size=37)
    rms = render_compressor(
        audio, rate, CompressorSettings(**{**settings.__dict__, "detector": "rms"})
    )
    assert peak.reduction_db[25] > peak.reduction_db[45]
    assert peak.reduction_db[70] < peak.reduction_db[80] <= 0
    assert np.min(peak.reduction_db) < np.min(rms.reduction_db)
    boosted = render_compressor(
        np.full((100, 1), 0.9), rate, CompressorSettings(ratio=1, makeup_db=6)
    ).audio
    assert np.max(boosted) > 1  # Manual makeup does not claim peak protection.


def test_neutral_compressor_is_sample_identical_and_steady_state_follows_curve():
    rate = 16000
    audio = np.random.default_rng(228).normal(0, 0.1, (rate, 2))
    np.testing.assert_array_equal(
        render_compressor(audio, rate, CompressorSettings(ratio=1)).audio, audio
    )
    steady = np.full((rate, 2), 0.5)
    settings = CompressorSettings(threshold_db=-18, ratio=4, attack_ms=5, knee_db=0)
    output = render_compressor(steady, rate, settings)
    target = gain_reduction_db(20 * np.log10(0.5), settings)
    assert output.reduction_db[-1] == pytest.approx(target, abs=1e-4)
    np.testing.assert_allclose(output.audio[-100:], 0.5 * 10 ** (target / 20), atol=1e-5)


@pytest.mark.parametrize(
    "field,value",
    [
        ("threshold_db", -61),
        ("ratio", 0),
        ("attack_ms", -1),
        ("release_ms", 0),
        ("knee_db", float("nan")),
        ("makeup_db", 30),
        ("detector", "unsupported"),
    ],
)
def test_invalid_settings_rejected(field, value):
    settings = CompressorSettings(**{**CompressorSettings().__dict__, field: value})
    with pytest.raises(ValueError):
        validate_settings(settings)


def test_downstream_cache_recipe_roundtrip_and_unsupported_dynamics(tmp_path):
    rate = 16000
    audio = np.random.default_rng(218).normal(0, 0.06, (rate, 2))
    file = tmp_path / "original.wav"
    sf.write(file, audio, rate, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(file))
    project.stages["eq"].bypassed = False
    eq = EQSettings((BellSettings(frequency=1800, gain_db=4),))
    project.stages["eq"].parameters = eq_parameters(eq)
    project.stages["dynamics"].bypassed = False
    settings = CompressorSettings(threshold_db=-28, ratio=3, attack_ms=8)
    project.stages["dynamics"].parameters = compressor_parameters(settings)
    renderer = ChainRenderer(audio, rate)
    first = renderer.render(project, block_size=73)
    before = render_eq(audio, rate, eq, block_size=73)
    np.testing.assert_array_equal(first.equalized, before)
    np.testing.assert_allclose(first.output, render_compressor(before, rate, settings, 73).audio)
    assert first.compression is not None
    assert first.compression.audio is first.output
    new_settings = CompressorSettings(threshold_db=-35, ratio=3, attack_ms=8)
    project.stages["dynamics"].parameters = compressor_parameters(new_settings)
    second = renderer.render(project)
    assert second.equalized is first.equalized
    assert renderer.computations == {"repair": 1, "match": 1, "eq": 1, "dynamics": 2}
    assert np.any(first.output != second.output)
    saved = tmp_path / "mix.omazone.json"
    save_project(saved, project)
    reopened = load_project(saved)
    assert settings_from_parameters(reopened.stages["dynamics"].parameters) == new_settings
    np.testing.assert_array_equal(ChainRenderer(audio, rate).render(reopened).output, second.output)
    reopened.stages["dynamics"].bypassed = True
    np.testing.assert_array_equal(ChainRenderer(audio, rate).render(reopened).output, before)
    # Earlier reserved dynamics metadata is retained but never silently rendered.
    reopened.stages["dynamics"].parameters = {"kind": "future-processor"}
    reopened.stages["dynamics"].bypassed = False
    with pytest.raises(ValueError, match="Unsupported dynamics"):
        ChainRenderer(audio, rate).render(reopened)
