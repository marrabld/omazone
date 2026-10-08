"""Shared render and matching-analysis status contracts."""

import pytest

from omazone.manual_eq import BellSettings, EQSettings, eq_parameters
from omazone.project import MatchCalibration, Project
from omazone.workflow_status import (
    AnalysisState,
    RenderState,
    derive_workflow_status,
    matching_analysis_state,
)

EQ_PARAMS = eq_parameters(EQSettings((BellSettings(gain_db=-2),)))


@pytest.mark.parametrize(
    ("source_loaded", "bypassed", "parameters", "rendered", "expected"),
    (
        (False, False, EQ_PARAMS, (), RenderState.BLOCKED),
        (True, True, EQ_PARAMS, (), RenderState.SKIPPED),
        (True, False, {}, (), RenderState.NOT_CONFIGURED),
        (True, False, EQ_PARAMS, (), RenderState.NEEDS_RENDER),
        (True, False, EQ_PARAMS, ("eq",), RenderState.READY),
    ),
)
def test_render_state_contract(source_loaded, bypassed, parameters, rendered, expected):
    project = Project()
    project.stages["eq"].bypassed = bypassed
    project.stages["eq"].parameters = parameters
    status = derive_workflow_status(
        project, source_loaded=source_loaded, rendered_stages=rendered
    ).stages["eq"]
    assert status.state is expected
    assert status.can_continue is (expected in (RenderState.SKIPPED, RenderState.READY))
    assert status.comparison_available is ("eq" in rendered)


def configured_matching_project():
    project = Project(match_mode="whole")
    project.calibration = MatchCalibration(project.config_key(), project.input_key())
    return project


def test_matching_analysis_contract():
    project = configured_matching_project()
    assert matching_analysis_state(project) is AnalysisState.CURRENT

    project.calibration = MatchCalibration(project.config_key(), "earlier-input")
    assert matching_analysis_state(project) is AnalysisState.RETAINED

    project.calibration = None
    assert matching_analysis_state(project) is AnalysisState.MISSING

    project.stages["match"].bypassed = True
    assert matching_analysis_state(project) is AnalysisState.CURRENT


def test_missing_matching_analysis_blocks_render_but_retained_analysis_does_not():
    project = configured_matching_project()
    project.stages["output"].bypassed = False
    project.stages["output"].parameters = {"kind": "output-gain-v1", "gain_db": -2}
    project.calibration = None
    missing = derive_workflow_status(project, source_loaded=True)
    assert missing.matching_analysis is AnalysisState.MISSING
    assert not missing.render_allowed
    assert missing.analysis_allowed
    assert missing.stages["match"].action == "Analyse"
    assert missing.stages["output"].action == "Analyse"

    project.calibration = MatchCalibration(project.config_key(), "earlier-input")
    retained = derive_workflow_status(project, source_loaded=True)
    assert retained.matching_analysis is AnalysisState.RETAINED
    assert retained.render_allowed
    assert retained.stages["match"].state is RenderState.NEEDS_RENDER


def test_repair_replay_failure_blocks_the_full_chain_unless_repair_is_skipped():
    project = Project()
    blocked = derive_workflow_status(project, source_loaded=True, repair_unavailable=True)
    assert not blocked.render_allowed
    assert all(item.state is RenderState.BLOCKED for item in blocked.stages.values())

    project.stages["repair"].bypassed = True
    skipped = derive_workflow_status(project, source_loaded=True, repair_unavailable=True)
    assert skipped.render_allowed
    assert skipped.stages["repair"].state is RenderState.SKIPPED


def test_final_render_controls_export_and_output_measurements():
    project = Project()
    project.stages["output"].bypassed = False
    project.stages["output"].parameters = {"kind": "output-gain-v1", "gain_db": -2}
    stale = derive_workflow_status(project, source_loaded=True)
    assert not stale.export_available
    assert not stale.stages["output"].measurements_available

    ready = derive_workflow_status(
        project,
        source_loaded=True,
        rendered_stages=("output",),
        final_render_current=True,
    )
    assert ready.export_available
    assert ready.stages["output"].state is RenderState.READY
    assert ready.stages["output"].measurements_available


def test_invalid_enabled_recipe_blocks_itself_and_downstream_stages():
    project = Project()
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = {"kind": "future-eq"}
    status = derive_workflow_status(project, source_loaded=True, final_render_current=True)
    assert status.stages["eq"].state is RenderState.BLOCKED
    assert status.stages["dynamics"].state is RenderState.BLOCKED
    assert status.stages["output"].state is RenderState.BLOCKED
    assert not status.render_allowed
    assert not status.analysis_allowed
    assert not status.export_available
    assert "saved eq settings" in status.render_reason.lower()


def test_enabled_unconfigured_stage_blocks_render_and_export():
    project = Project()
    project.stages["eq"].bypassed = False
    status = derive_workflow_status(project, source_loaded=True, final_render_current=True)
    assert status.stages["eq"].state is RenderState.NOT_CONFIGURED
    assert not status.render_allowed
    assert not status.analysis_allowed
    assert not status.export_available


def test_invalid_recognised_eq_recipe_blocks_render():
    project = Project()
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = eq_parameters(
        EQSettings((BellSettings(frequency=30000, gain_db=-2),))
    )
    status = derive_workflow_status(project, source_loaded=True, sample_rate=48000)
    assert status.stages["eq"].state is RenderState.BLOCKED
    assert not status.render_allowed
    assert "nyquist" in status.render_reason.lower()


def test_unavailable_enabled_repair_blocks_export():
    project = Project()
    status = derive_workflow_status(
        project,
        source_loaded=True,
        repair_unavailable=True,
        final_render_current=True,
    )
    assert not status.render_allowed
    assert not status.export_available
