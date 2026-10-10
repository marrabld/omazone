"""Every step must say what it is waiting on, and mean it."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtWidgets

from omazone.compressor import CompressorSettings, compressor_parameters
from omazone.gui import Window, load_audio
from omazone.output_gain import OutputGainSettings, output_parameters
from omazone.project import Project
from omazone.workflow_status import (
    RenderState,
    StageStatus,
    derive_workflow_status,
    nav_label,
)


def enabled(stage, parameters=None):
    project = Project()
    project.stages[stage].bypassed = False
    project.stages[stage].parameters = parameters or {}
    return project


@pytest.mark.parametrize("stage", ("dynamics", "output"))
def test_an_enabled_stage_with_no_saved_recipe_is_renderable(stage):
    """The settings constructors default an empty recipe, so it is a configuration.

    Reporting these as unconfigured disabled the render button and Continue for
    exactly the state a learner reaches by turning a stage on.
    """
    status = derive_workflow_status(enabled(stage), source_loaded=True)
    assert status.stages[stage].state is RenderState.NEEDS_RENDER
    assert status.render_allowed
    assert nav_label(status.stages[stage]) == "render"


def test_manual_eq_needs_a_band_that_will_actually_change_the_audio():
    """Manual EQ keeps the renderer's activity rule rather than a looser one.

    A muted band, or one at unity, passes audio through unchanged. Reporting
    those as configured would promise an adjustment the renderer cannot make.
    """
    from omazone.manual_eq import BellSettings, EQSettings, eq_parameters

    empty = derive_workflow_status(enabled("eq"), source_loaded=True)
    assert empty.stages["eq"].state is RenderState.NOT_CONFIGURED
    assert empty.stages["eq"].reason == "Add a band to use this step, or skip it."
    assert nav_label(empty.stages["eq"]) == "set up"

    unity = derive_workflow_status(
        enabled("eq", eq_parameters(EQSettings((BellSettings(gain_db=0),)))), source_loaded=True
    )
    assert unity.stages["eq"].state is RenderState.NOT_CONFIGURED

    audible = derive_workflow_status(
        enabled("eq", eq_parameters(EQSettings((BellSettings(gain_db=-2),)))), source_loaded=True
    )
    assert audible.stages["eq"].state is RenderState.NEEDS_RENDER
    assert audible.render_allowed
    assert nav_label(audible.stages["eq"]) == "render"


def test_repair_and_matching_never_block_the_chain():
    """They have no recipe of their own, so they pass through until they are used.

    Both default to enabled with nothing to apply, which reads as skipped rather
    than as something to set up, so they must never hold up Continue.
    """
    status = derive_workflow_status(enabled("repair"), source_loaded=True)
    repair = status.stages["repair"]
    assert repair.state is RenderState.SKIPPED
    assert repair.can_continue
    assert status.render_allowed
    assert "operations" in repair.reason

    project = Project()
    project.match_mode = "whole"
    matching = derive_workflow_status(project, source_loaded=True)
    assert matching.stages["match"].state is RenderState.NEEDS_RENDER
    assert matching.stages["match"].action == "Analyse"
    assert nav_label(matching.stages["match"]) == "analyse"


@pytest.mark.parametrize(
    ("state", "action", "expected"),
    (
        (RenderState.READY, "Compare", "ready"),
        (RenderState.SKIPPED, "Enable", "skipped"),
        (RenderState.NOT_CONFIGURED, "Configure", "set up"),
        (RenderState.NEEDS_RENDER, "Render", "render"),
        (RenderState.NEEDS_RENDER, "Analyse", "analyse"),
        (RenderState.BLOCKED, "Load", "load"),
        (RenderState.BLOCKED, "Relink", "relink"),
        (RenderState.BLOCKED, "Review settings", "review"),
        (RenderState.BLOCKED, "Repair", "repair"),
    ),
)
def test_navigation_names_the_action_it_is_waiting_on(state, action, expected):
    assert nav_label(StageStatus(state, "reason", action)) == expected


def test_navigation_wording_stays_short():
    """Naming the action must not make the bar wider than the old wording."""
    assert len(nav_label(StageStatus(RenderState.NEEDS_RENDER, "", "Configure"))) < len(
        "action needed"
    )


def test_an_empty_recipe_really_does_render_through_the_default_processor():
    """The whole fix rests on this: the renderer must honour what we now allow.

    If an enabled stage with no saved parameters produced silence, or a crash,
    then calling it configured would be wrong rather than helpful.
    """
    from omazone.pipeline import ChainRenderer

    rate = 16000
    tone = (np.sin(2 * np.pi * 220 * (np.arange(rate) / rate))[:, None] * 0.2).astype(np.float32)
    audio = np.concatenate([tone, tone * 0.7], axis=1)
    result = ChainRenderer(audio, rate, None).render(enabled("dynamics"))
    assert result.compression is not None
    assert result.compression.max_reduction_db > 0, "the default compressor must actually work"
    assert not np.array_equal(result.output, audio)
    assert compressor_parameters(CompressorSettings())["threshold_db"] == -18.0
    assert output_parameters(OutputGainSettings())["gain_db"] == 0.0


def test_every_action_the_model_emits_has_a_navigation_verb():
    """A renamed action would otherwise degrade silently to "action needed"."""
    from omazone.workflow_status import ACTION_VERB

    project = enabled("output")
    workflow = derive_workflow_status(project, source_loaded=True)
    for stage, status in workflow.stages.items():
        assert status.action in set(ACTION_VERB) | {"Enable", "Compare"}, (stage, status.action)
    assert workflow.render_action in set(ACTION_VERB) | {"Enable", "Compare"}


def test_turning_on_a_step_offers_a_working_action(tmp_path):
    """The reported bug: settings on screen, but a dead render button.

    Matches the state reached by clicking Enable on Dynamics, where the panel
    shows a threshold and a ratio yet the primary action is disabled and the
    status says the step needs configuring.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(271).normal(0, 0.2, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    try:
        window.show()
        window.loaded("source", load_audio(source))

        # Turn the step on from the navigation, exactly as a learner would.
        window.views.setStep("dynamics")
        assert window.skip_button.text() == "Enable Dynamics"
        window.skip_button.click()
        assert window.project.stages["dynamics"].bypassed is False
        assert window.views.currentStep() == "dynamics", "enabling stays put"

        # The panel shows a usable configuration...
        assert window.compressor_view.threshold.value() == -18.0
        assert window.compressor_view.ratio.value() == 2.0
        assert window.compressor_view.enabled.isChecked()

        # ...and the actions that depend on it must actually work.
        status = window.workflow_status().stages["dynamics"]
        assert status.state is RenderState.NEEDS_RENDER
        assert window.compressor_view.render_button.isEnabled()
        # Continue stays gated until the step is ready, and says why.
        assert not window.continue_button.isEnabled()
        window.go_continue()
        assert window.views.currentStep() == "dynamics", "Continue must not skip a render"
        assert (
            window.views.action_label.text() == window.workflow_status().stages["dynamics"].reason
        )
        label = window.views.navigation.tabText(window.views.indexOfStep("dynamics"))
        assert label == "Dynamics — render"
        assert not window.compressor_view.listen_button.isEnabled(), "nothing rendered yet"

        window.compressor_view.render_button.click()
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors
        # The render has to have done something, not merely completed.
        assert window.chain_result.compression.max_reduction_db > 0
        assert window.compressor_view.listen_button.isEnabled()
        label = window.views.navigation.tabText(window.views.indexOfStep("dynamics"))
        assert label == "Dynamics — ready"
        assert window.continue_button.isEnabled()
    finally:
        if window.worker is not None:
            window.worker.wait()
        window.close()
