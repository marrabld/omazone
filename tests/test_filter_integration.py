"""Corrective filter recipes, fixed chain order and retained upstream processing."""

import copy
import json
from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf

from omazone.comparison import Side, StagePair, resolve
from omazone.compressor import CompressorSettings
from omazone.dynamics import DynamicsSettings, dynamics_parameters, render_dynamics
from omazone.engine import MatchSettings, analyse, audition_pair, design_match, render
from omazone.filters import (
    FilterSettings,
    FiltersSettings,
    filter_parameters,
    filters_from_parameters,
    render_filters,
    validate_settings,
)
from omazone.limiter import LimiterSettings, limiter_parameters, render_limiter
from omazone.manual_eq import BellSettings, EQSettings, eq_parameters, render_eq
from omazone.multiband import MultibandSettings
from omazone.output_gain import OutputGainSettings, apply_output_gain, output_parameters
from omazone.pipeline import ChainRenderer
from omazone.project import AudioReference, MatchCalibration, Project, load_project, save_project
from omazone.render_payload import RenderPayload
from omazone.sections import capture_target
from omazone.waveform import SampleRegion
from omazone.workflow_status import RenderState, derive_workflow_status
from omazone.workflow_steps import resolve_step_key

RATE = 16000
RECIPE = FiltersSettings((
    FilterSettings(frequency=40, slope=24),
    FilterSettings(kind="notch", frequency=1000, q=10),
    FilterSettings(kind="lowpass", frequency=6000, slope=12, enabled=False),
))


@pytest.fixture
def chain(tmp_path):
    audio = np.random.default_rng(615).normal(0, 0.12, (8193, 2))
    audio[4000] = [0.9, -0.3]
    source = tmp_path / "original.wav"
    sf.write(source, audio, RATE, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(source), match_mode="whole")
    project.reference_target = capture_target(
        audio * 0.7, RATE, SampleRegion(0, len(audio)), "Target", "ref", "Generated"
    )
    project.matching = MatchSettings(amount=0.4, taps=129)
    spec = design_match(analyse(audio, RATE), project.reference_target.spectrum, RATE, project.matching)
    project.calibration = MatchCalibration(project.config_key(), project.input_key(), whole=spec)
    eq = EQSettings((BellSettings(frequency=2500, gain_db=-4),))
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = eq_parameters(eq)
    project.stages["dynamics"].bypassed = False
    dynamics = DynamicsSettings(broadband=CompressorSettings(threshold_db=-25, ratio=3))
    project.stages["dynamics"].parameters = dynamics_parameters(dynamics)
    project.stages["output"].bypassed = False
    project.stages["output"].parameters = output_parameters(OutputGainSettings(2))
    project.stages["limiter"].bypassed = False
    project.stages["limiter"].parameters = limiter_parameters(LimiterSettings(ceiling_db=-2))
    return audio, project, eq, dynamics


def enable_filters(project):
    project.stages["filters"].bypassed = False
    project.stages["filters"].parameters = filter_parameters(RECIPE)


@pytest.mark.parametrize("block_size", [1, 73, 4096])
def test_filter_prefix_sits_between_eq_and_dynamics_and_full_output_matches(chain, block_size):
    audio, project, eq, dynamics = chain
    enable_filters(project)
    result = ChainRenderer(audio, RATE).render(project, block_size)
    matched = render(audio, project.calibration.whole, block_size)
    equalized = render_eq(matched, RATE, eq, block_size=block_size)
    filtered = render_filters(equalized, RATE, RECIPE, block_size)
    compressed = render_dynamics(filtered, RATE, dynamics, block_size).audio
    gained = apply_output_gain(compressed, OutputGainSettings(2))
    output = render_limiter(gained, RATE, LimiterSettings(ceiling_db=-2), block_size).audio
    for actual, expected in (
        (result.matched, matched), (result.equalized, equalized), (result.filtered, filtered),
        (result.pre_output, compressed), (result.pre_limiter, gained), (result.output, output),
    ):
        np.testing.assert_array_equal(actual, expected)
    assert result.output.shape == audio.shape
    assert id(result.filtered) in result.loudness
    payload = RenderPayload.from_chain(result, project.calibration.whole, RATE)
    assert payload.chain is result and payload.output is result.output
    preview = audition_pair(equalized, filtered, RATE)
    for actual, wanted in zip(payload.filters_preview, preview, strict=True):
        np.testing.assert_array_equal(actual, wanted)
    preview = audition_pair(filtered, compressed, RATE)
    for actual, wanted in zip(payload.dynamics_preview, preview, strict=True):
        np.testing.assert_array_equal(actual, wanted)


def test_filter_edits_reuse_match_and_eq_while_eq_edits_recompute_filters(chain):
    audio, project, eq, _ = chain
    enable_filters(project)
    calibration = copy.deepcopy(project.calibration)
    eq_recipe = copy.deepcopy(project.stages["eq"].parameters)
    renderer = ChainRenderer(audio, RATE)
    first = renderer.render(project)
    updated = replace(RECIPE, filters=(replace(RECIPE.filters[0], frequency=80), *RECIPE.filters[1:]))
    project.stages["filters"].parameters = filter_parameters(updated)
    second = renderer.render(project)
    assert second.matched is first.matched and second.equalized is first.equalized
    assert second.filtered is not first.filtered
    assert renderer.computations == {
        "repair": 1, "match": 1, "eq": 1, "filters": 2, "dynamics": 2, "output": 2, "limiter": 2,
    }
    assert project.stages["eq"].parameters == eq_recipe
    assert project.can_render_saved_match and not project.needs_reanalysis
    np.testing.assert_array_equal(project.calibration.whole.coefficients, calibration.whole.coefficients)
    project.stages["eq"].parameters = eq_parameters(replace(eq, bands=(BellSettings(gain_db=3),)))
    third = renderer.render(project)
    assert third.matched is first.matched
    assert third.equalized is not second.equalized and third.filtered is not second.filtered
    assert renderer.computations == {
        "repair": 1, "match": 1, "eq": 2, "filters": 3, "dynamics": 3, "output": 3, "limiter": 3,
    }


def test_complete_filter_chain_is_block_size_independent(chain):
    audio, project, _, _ = chain
    enable_filters(project)
    expected = ChainRenderer(audio, RATE).render(project, 4096)
    for size in (1, 73):
        result = ChainRenderer(audio, RATE).render(project, size)
        np.testing.assert_allclose(result.filtered, expected.filtered, rtol=0, atol=1e-12)
        np.testing.assert_allclose(result.output, expected.output, rtol=0, atol=1e-12)


@pytest.mark.parametrize("mode", ["bypassed", "empty", "disabled"])
def test_inactive_filter_prefix_shares_eq_array_and_does_not_duplicate_metering(chain, mode):
    audio, project, _, _ = chain
    enable_filters(project)
    if mode == "bypassed":
        project.stages["filters"].bypassed = True
    elif mode == "empty":
        project.stages["filters"].parameters = {}
    else:
        project.stages["filters"].parameters = filter_parameters(
            replace(RECIPE, filters=tuple(replace(item, enabled=False) for item in RECIPE.filters))
        )
    renderer = ChainRenderer(audio, RATE)
    result = renderer.render(project)
    assert result.filtered is result.equalized
    payload = RenderPayload.from_chain(result, project.calibration.whole, RATE)
    assert payload.filters_preview is None, "Inactive filters must not allocate full-song previews"
    distinct = {id(value) for value in (
        result.original, result.repaired, result.matched, result.equalized,
        result.filtered, result.pre_output, result.pre_limiter, result.output,
    )}
    assert set(result.loudness) == distinct


def test_active_recipe_roundtrip_retains_full_precision_bypass_and_output(chain, tmp_path):
    audio, project, _, _ = chain
    enable_filters(project)
    precise = replace(RECIPE, filters=(FilterSettings(frequency=51.234567891, q=17.123456789),))
    project.stages["filters"].parameters = filter_parameters(precise)
    assert filters_from_parameters(project.stages["filters"].parameters) == precise
    validate_settings(precise, RATE)
    file = tmp_path / "session.omazone.json"
    save_project(file, project)
    loaded = load_project(file)
    assert loaded.stages["filters"] == project.stages["filters"]
    np.testing.assert_array_equal(
        ChainRenderer(audio, RATE).render(loaded).output,
        ChainRenderer(audio, RATE).render(project).output,
    )
    loaded.stages["filters"].bypassed = True
    save_project(file, loaded)
    again = load_project(file)
    assert again.stages["filters"].bypassed
    assert again.stages["filters"].parameters == project.stages["filters"].parameters


@pytest.mark.parametrize("mode", ["broadband", "three-band"])
def test_pre_filter_project_migrates_bypassed_and_matches_original_chain_exactly(chain, tmp_path, mode):
    audio, project, eq, dynamics = chain
    if mode == "three-band":
        dynamics = replace(dynamics, mode=mode, multiband=MultibandSettings())
        project.stages["dynamics"].parameters = dynamics_parameters(dynamics)
    # Reproduce the pre-filter recipe schema and old chain directly, without
    # calling the new filter-aware renderer for the expected result.
    expected = render(audio, project.calibration.whole)
    expected = render_eq(expected, RATE, eq)
    expected = render_dynamics(expected, RATE, dynamics).audio
    expected = apply_output_gain(expected, OutputGainSettings(2))
    expected = render_limiter(expected, RATE, LimiterSettings(ceiling_db=-2)).audio
    file = tmp_path / "pre-filters.omazone.json"
    save_project(file, project)
    data = json.loads(file.read_text())
    del data["stages"]["filters"]
    file.write_text(json.dumps(data))
    loaded = load_project(file)
    assert loaded.stages["filters"].bypassed and loaded.stages["filters"].parameters == {}
    assert not loaded.needs_reanalysis
    result = ChainRenderer(audio, RATE).render(loaded)
    assert result.filtered is result.equalized
    np.testing.assert_array_equal(result.output, expected)


@pytest.mark.parametrize("parameters", [
    None, [], {"kind": "filters-v2", "filters": []}, {"filters": []},
    {"kind": "filters-v1", "filters": None}, {"kind": "filters-v1", "filters": [{}]},
    {"kind": "filters-v1", "filters": [], "extra": True},
    {"kind": "filters-v1", "filters": [None]},
    filter_parameters(FiltersSettings((FilterSettings(),) * 7)),
])
def test_unknown_or_malformed_filter_recipes_are_not_replaced(parameters):
    original = copy.deepcopy(parameters)
    with pytest.raises(ValueError):
        filters_from_parameters(parameters)
    assert parameters == original


@pytest.mark.parametrize("bypassed", [False, True])
def test_invalid_saved_values_are_rejected_even_when_bypassed(chain, tmp_path, bypassed):
    _, project, _, _ = chain
    file = tmp_path / "invalid.omazone.json"
    save_project(file, project)
    data = json.loads(file.read_text())
    data["stages"]["filters"] = {
        "bypassed": bypassed,
        "parameters": filter_parameters(FiltersSettings((FilterSettings(frequency=RATE),))),
    }
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Nyquist"):
        load_project(file)


@pytest.mark.parametrize("state", ["skip", "empty", "disabled", "valid", "invalid", "ready"])
def test_filter_workflow_configuration_and_comparison_boundaries(state):
    project = Project()
    enable_filters(project)
    if state == "skip":
        project.stages["filters"].bypassed = True
    if state == "empty":
        project.stages["filters"].parameters = {}
    if state == "disabled":
        project.stages["filters"].parameters = filter_parameters(
            FiltersSettings((FilterSettings(enabled=False),))
        )
    if state == "invalid":
        project.stages["filters"].parameters = {"kind": "unknown"}
    workflow = derive_workflow_status(
        project, source_loaded=True, sample_rate=RATE,
        rendered_stages=("filters",) if state == "ready" else (),
    )
    expected = {
        "skip": RenderState.SKIPPED, "empty": RenderState.NOT_CONFIGURED,
        "disabled": RenderState.NOT_CONFIGURED, "valid": RenderState.NEEDS_RENDER,
        "invalid": RenderState.BLOCKED, "ready": RenderState.READY,
    }
    assert workflow.stages["filters"].state is expected[state]
    assert workflow.render_allowed == (state in ("skip", "valid", "ready"))
    if state in ("empty", "disabled", "invalid"):
        assert workflow.blocking_stage == "filters"
    if state == "invalid":
        assert workflow.stages["dynamics"].state is RenderState.BLOCKED
    assert resolve_step_key("filters") == "filters"


def test_stale_filter_comparison_keeps_eq_input_and_dynamics_prefers_rendered_filters():
    eq, filtered = np.ones((2000, 2)), np.full((2000, 2), 0.5)
    pairs = {"eq": StagePair(None, eq), "filters": StagePair(eq, filtered)}
    dynamics = resolve("dynamics", pairs, rate=RATE)
    assert dynamics.before is filtered
    state = resolve("filters", pairs, rate=RATE, side=Side.AFTER)
    assert state.before is eq and state.after is filtered and state.available
    assert state.side_label == "after filters"
    pairs["filters"] = StagePair(eq, None)
    stale = resolve("filters", pairs, rate=RATE)
    assert stale.before is eq and not stale.available
    assert resolve("dynamics", pairs, rate=RATE).before is eq
