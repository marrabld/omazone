"""Dynamic EQ chain placement, upstream retention and additive project migration."""

import copy
import json
from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf

from omazone.dynamic_eq import DynamicEQSettings, dynamic_eq_parameters, render_dynamic_eq
from omazone.dynamics import default_settings, dynamics_parameters, render_dynamics
from omazone.engine import MatchSettings, analyse, audition_pair, design_match, render
from omazone.filters import FilterSettings, FiltersSettings, filter_parameters, render_filters
from omazone.limiter import LimiterSettings, limiter_parameters, render_limiter
from omazone.manual_eq import BellSettings, EQSettings, eq_parameters, render_eq
from omazone.output_gain import OutputGainSettings, apply_output_gain, output_parameters
from omazone.pipeline import ChainRenderer
from omazone.project import AudioReference, MatchCalibration, Project, load_project, save_project
from omazone.render_payload import RenderPayload
from omazone.sections import capture_target
from omazone.waveform import SampleRegion

RATE = 16000


@pytest.fixture
def chain(tmp_path):
    audio = np.random.default_rng(800).normal(0, 0.2, (8193, 2))
    path = tmp_path / "mix.wav"
    sf.write(path, audio, RATE, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(path), match_mode="whole")
    project.reference_target = capture_target(
        audio * 0.8, RATE, SampleRegion(0, len(audio)), "Target", "ref", "Generated"
    )
    project.matching = MatchSettings(amount=0.4, taps=129)
    spec = design_match(
        analyse(audio, RATE), project.reference_target.spectrum, RATE, project.matching
    )
    project.calibration = MatchCalibration(project.config_key(), project.input_key(), whole=spec)
    eq = EQSettings((BellSettings(frequency=1800, gain_db=-3),))
    filters = FiltersSettings((FilterSettings(frequency=80),))
    dynamic_eq = DynamicEQSettings(frequency=2000, q=2, threshold_db=-30, max_cut_db=6)
    for stage, parameters in (
        ("eq", eq_parameters(eq)),
        ("filters", filter_parameters(filters)),
        ("dynamic-eq", dynamic_eq_parameters(dynamic_eq)),
        ("output", output_parameters(OutputGainSettings(2))),
        ("limiter", limiter_parameters(LimiterSettings(-2))),
    ):
        project.stages[stage].bypassed = False
        project.stages[stage].parameters = parameters
    return audio, project, eq, filters, dynamic_eq


@pytest.mark.parametrize("mode", ["broadband", "three-band"])
def test_full_chain_matches_direct_processing_with_either_compressor(chain, mode):
    audio, project, eq, filters, settings = chain
    dynamics = replace(default_settings(RATE), mode=mode)
    project.stages["dynamics"].bypassed = False
    project.stages["dynamics"].parameters = dynamics_parameters(dynamics)
    result = ChainRenderer(audio, RATE).render(project, 73)
    matched = render(audio, project.calibration.whole, 73)
    equalized = render_eq(matched, RATE, eq, block_size=73)
    filtered = render_filters(equalized, RATE, filters, 73)
    dynamic = render_dynamic_eq(filtered, RATE, settings, 73)
    compressed = render_dynamics(dynamic.audio, RATE, dynamics, 73).audio
    gained = apply_output_gain(compressed, OutputGainSettings(2))
    limited = render_limiter(gained, RATE, LimiterSettings(-2), 73).audio
    for actual, expected in (
        (result.matched, matched),
        (result.equalized, equalized),
        (result.filtered, filtered),
        (result.dynamic_equalized, dynamic.audio),
        (result.pre_output, compressed),
        (result.output, limited),
    ):
        np.testing.assert_array_equal(actual, expected)
    assert result.dynamic_eq is not None
    assert id(result.dynamic_equalized) in result.loudness
    payload = RenderPayload.from_chain(result, project.calibration.whole, RATE)
    for actual, expected in zip(
        payload.dynamic_eq_preview,
        audition_pair(filtered, dynamic.audio, RATE, result.loudness),
        strict=True,
    ):
        np.testing.assert_array_equal(actual, expected)
    for actual, expected in zip(
        payload.dynamics_preview,
        audition_pair(dynamic.audio, compressed, RATE, result.loudness),
        strict=True,
    ):
        np.testing.assert_array_equal(actual, expected)


def test_downstream_edits_reuse_prefixes_and_preserve_every_upstream_choice(chain):
    audio, project, _, _, settings = chain
    calibration = copy.deepcopy(project.calibration)
    recipes = copy.deepcopy(project.stages)
    renderer = ChainRenderer(audio, RATE)
    first = renderer.render(project)
    project.stages["dynamic-eq"].parameters = dynamic_eq_parameters(
        replace(settings, frequency=3000)
    )
    second = renderer.render(project)
    assert second.matched is first.matched and second.equalized is first.equalized
    assert second.filtered is first.filtered and second.dynamic_eq is not first.dynamic_eq
    for key in ("repair", "match", "eq", "filters"):
        assert renderer.computations[key] == 1
        assert project.stages[key] == recipes[key]
    assert renderer.computations["dynamic-eq"] == 2
    project.stages["output"].parameters = output_parameters(OutputGainSettings(-2))
    third = renderer.render(project)
    assert third.dynamic_eq is second.dynamic_eq
    assert project.calibration.config_key == calibration.config_key
    assert project.calibration.input_key == calibration.input_key
    np.testing.assert_array_equal(
        project.calibration.whole.coefficients, calibration.whole.coefficients
    )
    assert not project.needs_reanalysis


def test_recipe_roundtrip_retains_precision_bypass_and_legacy_projects_stay_unchanged(
    chain, tmp_path
):
    audio, project, _, _, settings = chain
    settings = replace(settings, frequency=2345.6789123, q=1.123456789)
    project.stages["dynamic-eq"].parameters = dynamic_eq_parameters(settings)
    file = tmp_path / "project.json"
    save_project(file, project)
    loaded = load_project(file)
    assert loaded.stages["dynamic-eq"] == project.stages["dynamic-eq"]
    expected = ChainRenderer(audio, RATE).render(project)
    actual = ChainRenderer(audio, RATE).render(loaded)
    np.testing.assert_array_equal(actual.output, expected.output)
    data = json.loads(file.read_text())
    del data["stages"]["dynamic-eq"]
    file.write_text(json.dumps(data))
    legacy = load_project(file)
    assert legacy.stages["dynamic-eq"].bypassed
    result = ChainRenderer(audio, RATE).render(legacy)
    assert result.dynamic_eq is None and result.dynamic_equalized is result.filtered
    payload = RenderPayload.from_chain(result, legacy.calibration.whole, RATE)
    assert payload.dynamic_eq_preview is None
    project.stages["dynamic-eq"].bypassed = True
    np.testing.assert_array_equal(result.output, ChainRenderer(audio, RATE).render(project).output)


def test_malformed_new_recipe_is_rejected_instead_of_migrated(chain, tmp_path):
    _, project, _, _, _ = chain
    file = tmp_path / "project.json"
    save_project(file, project)
    data = json.loads(file.read_text())
    data["stages"]["dynamic-eq"]["parameters"] = {"kind": "dynamic-eq-v99"}
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_project(file)
