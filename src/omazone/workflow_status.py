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


def describe(state):
    """The three words the chain summary uses for a render state."""
    if state is RenderState.READY:
        return "ready"
    if state is RenderState.SKIPPED:
        return "skipped"
    return "action needed"


# The action a stage is waiting on, as the learner would name it. The model
# already works this out; the navigation should not reduce it to one phrase.
ACTION_VERB = {
    "Load": "load",
    "Relink": "relink",
    "Repair": "repair",
    "Review settings": "review",
    "Configure": "set up",
    "Analyse": "analyse",
    "Render": "render",
}


def nav_label(status):
    """What a navigation entry shows: a state, or the action it is waiting on.

    Naming the action turns the bar into a to-do list, and reads shorter than
    calling everything "action needed".
    """
    if status.state is RenderState.READY:
        return "ready"
    if status.state is RenderState.SKIPPED:
        return "skipped"
    return ACTION_VERB.get(status.action, describe(status.state))


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
    render_action: str = "Render"
    blocking_stage: str | None = None


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


# What is still missing when a stage is enabled but holds no recipe of its own.
# Only Manual EQ reaches this today: Repair and Matching pass through until used,
# and the processor stages all default to a recipe the renderer accepts. The
# fallback keeps a newly added stage from failing outright.
CONFIGURE_GUIDANCE = {
    "repair": "Find and accept peaks to repair, or skip this step.",
    "match": "Load a reference and analyse it, or skip this step.",
    "eq": "Add a band to use this step, or skip it.",
    "dynamics": "Set threshold and ratio, then render.",
    "output": "Set output gain, then render.",
}


def stage_configured(project, stage, sample_rate=48000):
    """Whether a stage holds a recipe the renderer would accept.

    An enabled stage with no saved parameters is not unconfigured: the settings
    constructors return a complete default recipe for an empty one, and the
    renderer uses it. Reporting those stages as unconfigured would block a render
    that would work, leaving the learner staring at a disabled action.
    """
    if stage == "repair":
        return bool(project.repairs)
    if stage == "match":
        return project.match_mode != "none"
    parameters = project.stages[stage].parameters
    if stage == "eq":
        settings = eq_from_parameters(parameters)
        validate_eq(settings, sample_rate, project.regions)
        # Mirror the renderer's own activity rule: a muted band, or one at unity,
        # passes audio through unchanged, so calling that configured would promise
        # an adjustment the renderer cannot make.
        return any(band.enabled and band.gain_db != 0 for band in settings.bands)
    if stage == "dynamics":
        validate_compressor(compressor_settings(parameters))
        return True
    if stage == "output":
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
            reason = CONFIGURE_GUIDANCE.get(stage, f"Configure or skip {stage_name(stage)}.")
            action = "Configure"
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
    blocking = None
    final_current = bool(final_render_current and source_loaded)
    if not source_loaded:
        render_reason = source_reason
        render_action = "Relink" if project.source else "Load"
    elif repair_blocked:
        render_reason = statuses["repair"].reason
        render_action, blocking = statuses["repair"].action, "repair"
    elif first_invalid is not None:
        render_reason = statuses[first_invalid].reason
        render_action, blocking = statuses[first_invalid].action, first_invalid
    elif not_configured:
        first_missing = next(stage for stage in STAGES if stage in not_configured)
        render_reason = statuses[first_missing].reason
        render_action, blocking = statuses[first_missing].action, first_missing
    elif matching_blocked:
        render_reason = statuses["match"].reason
        render_action, blocking = statuses["match"].action, "match"
    elif not final_current:
        render_reason, render_action = "Render the saved recipe.", "Render"
        # Nothing is blocking, but the audio is stale. Point at the earliest step
        # that still owes a render. With every processor skipped the whole chain is
        # a no-op, so Output is where the recipe gets rendered.
        blocking = next(
            (stage for stage in STAGES if statuses[stage].state is RenderState.NEEDS_RENDER),
            "output",
        )
    else:
        render_reason, render_action = "Render the saved recipe.", "Render"
    return WorkflowStatus(
        statuses,
        analysis,
        final_current,
        final_current and render_allowed,
        render_allowed,
        analysis_allowed,
        render_reason,
        render_action,
        blocking,
    )


def stage_name(stage):
    return {
        "repair": "Repair",
        "match": "Matching",
        "eq": "Manual EQ",
        "dynamics": "Dynamics",
        "output": "Output",
    }[stage]
