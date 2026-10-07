"""Output gain, raw sample peaks and saved final-prefix behaviour."""

import numpy as np
import pytest
import soundfile as sf

from omazone.compressor import CompressorSettings, compressor_parameters
from omazone.manual_eq import BellSettings, EQSettings, eq_parameters
from omazone.output_gain import (
    OutputGainSettings,
    apply_output_gain,
    output_parameters,
    sample_peak,
    sample_peak_db,
    settings_from_parameters,
    validate_settings,
)
from omazone.pipeline import ChainRenderer
from omazone.project import AudioReference, Project, load_project, save_project


def test_explicit_gain_and_raw_sample_peaks_do_not_limit_or_normalise():
    audio = np.array([[0.5, -1.2], [-0.25, 1.0], [0.0, 0.0]])
    assert sample_peak(audio) == 1.2
    assert sample_peak_db(audio) == pytest.approx(20 * np.log10(1.2))
    np.testing.assert_array_equal(apply_output_gain(audio, OutputGainSettings()), audio)
    attenuated = apply_output_gain(audio, OutputGainSettings(-6))
    np.testing.assert_allclose(attenuated, audio * 10 ** (-6 / 20))
    assert sample_peak(attenuated) < 1
    boosted = apply_output_gain(audio, OutputGainSettings(3))
    assert sample_peak(boosted) > sample_peak(audio)
    assert sample_peak_db(np.zeros((3, 1))) == float("-inf")
    # Attenuating a flat-topped source changes level, not the missing peaks.
    clipped = np.clip(np.array([0.2, 0.9, 1.5, 1.6])[:, None], -1, 1)
    np.testing.assert_array_equal(
        apply_output_gain(clipped, OutputGainSettings(-6))[-2:, 0], np.full(2, 10 ** (-6 / 20))
    )


@pytest.mark.parametrize("gain", [float("nan"), float("inf"), -37, 19, True, "-2"])
def test_invalid_gain_fails_without_silent_changes(gain):
    with pytest.raises(ValueError):
        validate_settings(OutputGainSettings(gain))


def test_output_gain_only_recomputes_final_prefix_and_roundtrips(tmp_path):
    rate = 16000
    audio = np.random.default_rng(370).normal(0, 0.08, (rate, 2))
    file = tmp_path / "mix.wav"
    sf.write(file, audio, rate, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(file))
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = eq_parameters(EQSettings((BellSettings(gain_db=3),)))
    project.stages["dynamics"].bypassed = False
    project.stages["dynamics"].parameters = compressor_parameters(CompressorSettings())
    project.stages["output"].bypassed = False
    project.stages["output"].parameters = output_parameters(OutputGainSettings(4))
    renderer = ChainRenderer(audio, rate)
    first = renderer.render(project)
    assert first.pre_output is first.compression.audio
    np.testing.assert_allclose(first.output, first.pre_output * 10 ** (4 / 20))
    project.stages["output"].parameters = output_parameters(OutputGainSettings(-3))
    second = renderer.render(project)
    assert first.pre_output is second.pre_output
    assert first.compression is second.compression
    assert renderer.computations == {"repair": 1, "match": 1, "eq": 1, "dynamics": 1, "output": 2}
    np.testing.assert_allclose(second.output, first.pre_output * 10 ** (-3 / 20))
    saved = tmp_path / "output.omazone.json"
    save_project(saved, project)
    reopened = load_project(saved)
    assert settings_from_parameters(reopened.stages["output"].parameters) == OutputGainSettings(-3)
    np.testing.assert_array_equal(ChainRenderer(audio, rate).render(reopened).output, second.output)
    reopened.stages["output"].bypassed = True
    np.testing.assert_array_equal(
        ChainRenderer(audio, rate).render(reopened).output, first.pre_output
    )


def test_eq_can_introduce_overload_into_clean_source_then_output_gain_reduces_it():
    rate = 16000
    t = np.arange(rate) / rate
    source = (0.65 * np.sin(2 * np.pi * 1000 * t))[:, None]
    project = Project()
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = eq_parameters(
        EQSettings((BellSettings(frequency=1000, gain_db=6, q=1),))
    )
    project.stages["output"].bypassed = False
    project.stages["output"].parameters = output_parameters(OutputGainSettings(-6))
    result = ChainRenderer(source, rate).render(project)
    assert sample_peak(result.original) < 1
    assert sample_peak(result.pre_output) > 1
    assert sample_peak(result.output) < 1
    assert len(result.output) == len(source)


def test_unsupported_reserved_output_recipe_is_retained_but_never_applied(tmp_path):
    audio = np.full((16000, 1), 0.25)
    path = tmp_path / "mix.wav"
    sf.write(path, audio, 16000, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(path))
    project.stages["output"].parameters = {"ceiling_db": -1}
    saved = tmp_path / "legacy.omazone.json"
    save_project(saved, project)
    restored = load_project(saved)
    assert restored.stages["output"].parameters == {"ceiling_db": -1}
    np.testing.assert_array_equal(ChainRenderer(audio, 16000).render(restored).output, audio)
    restored.stages["output"].bypassed = False
    with pytest.raises(ValueError, match="Unsupported output recipe"):
        ChainRenderer(audio, 16000).render(restored)
