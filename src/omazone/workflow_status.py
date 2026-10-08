"""Pure workflow status derived from a project recipe and transient render artifacts."""

from dataclasses import dataclass
from enum import Enum

from .compressor import settings_from_parameters as compressor_settings
from .compressor import validate_settings as validate_compressor
from .manual_eq import eq_from_parameters, validate_eq
from .output_gain import settings_from_parameters as output_settings
from .output_gain import validate_settings as validate_output
from .project import STAGES, Project


class RenderState(str, Enum):
    BLOCKED = "blocked"
    NOT_CONFIGURED = "not-configured"
    SKIPPED = "skipped"
    NEEDS_RENDER = "needs-render"
    READY = "ready"


class AnalysisState(str, Enum):
    CURRENT = "current"
    RETAINED = "retained"
    MISSING = "missing"


@dataclass(frozen=True)
class StageStatus:
    state: RenderState
    reason: str
    action: str
    comparison_available: bool = False
    measurements_available: bool = False

    @property
    def can_continue(self):
        return self.state in (RenderState.SKIPPED, RenderState.READY)


@dataclass(frozen=True)
class WorkflowStatus:
    stages: dict[str, StageStatus]
    matching_analysis: AnalysisState
    final_render_current: bool
    export_available: bool
    render_allowed: bool
    analysis_allowed: bool
    render_reason: str


def matching_analysis_state(project):
    match = project.stages["match"]
    if match.bypassed or project.match_mode == "none":
        return AnalysisState.CURRENT
    calibration = project.calibration
    if calibration is None or calibration.config_key != project.config_key():
        return AnalysisState.MISSING
    if calibration.input_key != project.input_key():
        return AnalysisState.RETAINED
    return AnalysisState.CURRENT


def stage_configured(project, stage, sample_rate=48000):
    if stage == "repair":
        return bool(project.repairs)
    if stage == "match":
        return project.match_mode != "none"
    parameters = project.stages[stage].parameters
    if stage == "eq":
        settings = eq_from_parameters(parameters)
        validate_eq(settings, sample_rate, project.regions)
        return any(band.enabled and band.gain_db != 0 for band in settings.bands)
    if stage == "dynamics":
        if not parameters:
            return False
        validate_compressor(compressor_settings(parameters))
        return True
    if stage == "output":
        if not parameters:
            return False
        validate_output(output_settings(parameters))
        return True
    return bool(parameters)


def stage_skipped(project, stage):
    return (
        project.stages[stage].bypassed
        or (stage == "repair" and not project.repairs)
        or (stage == "match" and project.match_mode == "none")
    )


def configuration_error(project, stage, sample_rate):
    if stage_skipped(project, stage):
        return None
    try:
        stage_configured(project, stage, sample_rate)
    except ValueError as error:
        return str(error)
    return None


def derive_workflow_status(
    project: Project,
    *,
    source_loaded: bool,
    repair_unavailable: bool = False,
    rendered_stages=(),
    final_render_current: bool = False,
    sample_rate: int = 48000,
):
    rendered = frozenset(rendered_stages)
    analysis = matching_analysis_state(project)
    source_reason = (
        "Relink the original recording." if project.source is not None else "Load a recording."
    )
    repair_blocked = repair_unavailable and not project.stages["repair"].bypassed
    matching_required = not project.stages["match"].bypassed and project.match_mode != "none"
    matching_blocked = matching_required and analysis is AnalysisState.MISSING
    configuration_errors = {
        stage: error
        for stage in STAGES
        if (error := configuration_error(project, stage, sample_rate)) is not None
    }
    first_invalid = next((stage for stage in STAGES if stage in configuration_errors), None)
    not_configured = {
        stage
        for stage in STAGES
        if not stage_skipped(project, stage)
        and stage not in configuration_errors
        and not stage_configured(project, stage, sample_rate)
    }
    statuses = {}

    for stage in STAGES:
        available = stage in rendered
        if not source_loaded:
            state = RenderState.BLOCKED
            reason, action = source_reason, "Relink" if project.source else "Load"
        elif repair_blocked:
            state = RenderState.BLOCKED
            reason, action = "Saved repair cannot be replayed. Reassess or skip Repair.", "Repair"
        elif stage in configuration_errors:
            state = RenderState.BLOCKED
            reason, action = configuration_errors[stage], "Review settings"
        elif first_invalid is not None and STAGES.index(stage) > STAGES.index(first_invalid):
            state = RenderState.BLOCKED
            reason, action = (
                f"Resolve {stage_name(first_invalid)} settings first.",
                "Review settings",
            )
        elif stage_skipped(project, stage):
            state = RenderState.SKIPPED
            if stage == "repair" and not project.repairs:
                reason, action = "No repair operations; the original passes unchanged.", "Configure"
            elif stage == "match" and project.match_mode == "none":
                reason, action = (
                    "Matching has no target, so it passes unchanged. Add a reference to analyse it.",
                    "Configure",
                )
            else:
                reason, action = f"{stage_name(stage)} is skipped.", "Enable"
        elif stage in not_configured:
            state = RenderState.NOT_CONFIGURED
            reason, action = f"Configure or skip {stage_name(stage)}.", "Configure"
        elif stage in ("match", "eq", "dynamics", "output") and matching_blocked:
            state = RenderState.NEEDS_RENDER
            reason, action = "Analyse or skip Matching before rendering.", "Analyse"
        elif available:
            state = RenderState.READY
            reason, action = f"{stage_name(stage)} is ready.", "Compare"
        else:
            state = RenderState.NEEDS_RENDER
            reason, action = f"Render {stage_name(stage)} to update this result.", "Render"
        statuses[stage] = StageStatus(
            state,
            reason,
            action,
            comparison_available=available,
            measurements_available=available and (state is RenderState.READY or stage == "output"),
        )

    render_allowed = (
        source_loaded
        and not repair_blocked
        and not matching_blocked
        and not configuration_errors
        and not not_configured
    )
    analysis_allowed = (
        source_loaded
        and not repair_blocked
        and not configuration_errors
        and not (not_configured - {"match"})
    )
    if not source_loaded:
        render_reason = source_reason
    elif repair_blocked:
        render_reason = statuses["repair"].reason
    elif first_invalid is not None:
        render_reason = statuses[first_invalid].reason
    elif not_configured:
        first_missing = next(stage for stage in STAGES if stage in not_configured)
        render_reason = statuses[first_missing].reason
    elif matching_blocked:
        render_reason = statuses["match"].reason
    else:
        render_reason = "Render the saved recipe."
    final_current = bool(final_render_current and source_loaded)
    return WorkflowStatus(
        statuses,
        analysis,
        final_current,
        final_current and render_allowed,
        render_allowed,
        analysis_allowed,
        render_reason,
    )


def stage_name(stage):
    return {
        "repair": "Repair",
        "match": "Matching",
        "eq": "Manual EQ",
        "dynamics": "Dynamics",
        "output": "Output",
    }[stage]
