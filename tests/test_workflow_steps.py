"""Workflow steps are named, so navigation can be reordered without remapping."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtWidgets

from omazone.gui import Window, load_audio
from omazone.tool_panel import ToolPanel
from omazone.workflow_steps import (
    BY_KEY,
    KEYS,
    LEGACY_ORDER,
    NAVIGATION,
    STEPS,
    WORKFLOW,
    clamp,
    is_legacy_step_key,
    is_step_key,
    legacy_step_key,
    resolve_step_key,
    step_for,
)


def test_step_names_are_unique_and_lookups_are_strict():
    assert len(set(KEYS)) == len(KEYS)
    assert set(BY_KEY) == set(KEYS)
    with pytest.raises(KeyError):
        step_for("declipping")


def test_every_workflow_step_that_exists_is_known():
    unknown = set(WORKFLOW) - set(KEYS)
    # Export is added with the Export step; until then it has no page.
    assert unknown <= {"export"}


def test_every_step_resolves_to_a_workflow_position():
    """A step whose name is not in the workflow would make workflow_index raise."""
    for step in STEPS:
        assert step.part_of in BY_KEY or step.key in WORKFLOW
        assert 0 <= step.workflow_index < len(WORKFLOW)
    assert BY_KEY["reference"].part_of == "match"
    assert BY_KEY["sections"].part_of == "match"
    assert BY_KEY["match"].part_of is None


def test_processor_steps_name_the_stage_they_explain():
    stages = {step.key: step.stage for step in STEPS if step.stage}
    assert stages == {
        "match": "match",
        "reference": "match",
        "sections": "match",
        "repair": "repair",
        "eq": "eq",
        "dynamics": "dynamics",
        "output": "output",
    }
    assert BY_KEY["listen"].stage is None


def test_secondary_pages_share_their_parents_workflow_position():
    assert BY_KEY["reference"].workflow_index == BY_KEY["match"].workflow_index
    assert BY_KEY["sections"].workflow_index == BY_KEY["match"].workflow_index


def test_only_processor_steps_are_skippable():
    assert BY_KEY["listen"].skippable is False
    assert all(BY_KEY[key].skippable for key in ("repair", "match", "eq", "dynamics", "output"))


def test_spectral_pages_are_the_ones_that_plot_a_response():
    assert {step.key for step in STEPS if step.spectral} == {
        "match",
        "eq",
        "dynamics",
        "output",
    }


def test_saved_step_names_are_validated_and_unknown_ones_rejected():
    assert is_step_key("repair")
    assert not is_step_key("Repair")
    assert not is_step_key(4)
    assert not is_step_key(None)


def test_legacy_tab_positions_still_resolve():
    """Projects saved before steps were named must keep the right page."""
    assert is_legacy_step_key("0")
    assert not is_legacy_step_key(str(len(LEGACY_ORDER)))
    assert not is_legacy_step_key("repair")
    assert legacy_step_key("0") == LEGACY_ORDER[0]
    assert legacy_step_key(0) == LEGACY_ORDER[0]
    assert legacy_step_key(len(LEGACY_ORDER)) is None
    assert legacy_step_key("repair") == "repair"
    assert legacy_step_key("nonsense") is None


def test_legacy_positions_do_not_follow_the_live_navigation():
    """Reordering navigation must not move where an old session opens."""
    assert LEGACY_ORDER == (
        "match",
        "listen",
        "reference",
        "sections",
        "repair",
        "eq",
        "dynamics",
        "output",
    )
    # A session saved on tab 4 keeps meaning Repair even once live order changes.
    assert legacy_step_key(4) == "repair"


def test_clamp_falls_back_to_the_first_step():
    assert clamp("output") == "output"
    assert clamp("0") == KEYS[0]
    assert clamp(None) == KEYS[0]
    assert clamp(99) == KEYS[0]


def test_registered_pages_match_the_step_registry():
    """The registry only helps if the panels actually register those steps."""
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    try:
        assert window.views.step_keys == list(KEYS)
        assert window.views.navigation.count() == window.views.count() == len(KEYS)
        # Secondary pages belong to Match and carry no navigation entry.
        assert window.views.visibleSteps() == [step.key for step in NAVIGATION]
        for index, step in enumerate(STEPS):
            assert window.views.navigation.tabText(index).startswith(step.title)
            window.views.setStep(step.key)
            assert window.views.currentStep() == step.key
            assert window.views.currentIndex() == index
    finally:
        window.close()


def test_a_step_must_be_named_when_a_page_is_registered():
    """Silently defaulting a missing step would hide a mis-wired page."""
    panel = ToolPanel()
    with pytest.raises(ValueError, match="workflow step"):
        panel.addTab(QtWidgets.QWidget(), None)


def test_current_step_survives_a_bar_edited_behind_the_panel():
    panel = ToolPanel()
    page = QtWidgets.QWidget()
    panel.addTab(page, step_for("repair"))
    panel.navigation.insertTab(0, "Smuggled")
    # The panel cannot stop someone editing its public bar, but it must not lie.
    assert panel.currentStep() in BY_KEY


def test_navigation_accepts_the_comparison_name_for_a_step():
    """ "output" and "output-gain" name one step; neither spelling may dead-end."""
    window = Window()
    try:
        for key, alias in (("output", "output-gain"), ("match", "mastering")):
            assert window.views.indexOfStep(alias) == window.views.indexOfStep(key)
            assert window.views.setStep(alias) is True
            assert window.views.currentStep() == key
            assert window.views.setStep(key) is True
            assert window.views.currentStep() == key
        # A stage with no comparison name still navigates by its own key.
        assert window.views.setStep("eq") is True
        assert window.views.currentStep() == "eq"
        assert window.views.setStep("nonsense") is False
    finally:
        window.close()
        QtWidgets.QApplication.instance().processEvents()


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("output", "output"),
        ("output-gain", "output"),
        ("match", "match"),
        ("mastering", "match"),
        ("nonsense", None),
        (None, None),
    ],
)
def test_a_comparison_name_resolves_to_its_step(given, expected):
    assert resolve_step_key(given) == expected


def test_a_saved_selection_written_as_a_comparison_name_still_lands_on_that_step():
    """A session saved with the comparison spelling must not reset to the first step."""
    assert clamp("output-gain") == "output"
    assert clamp("mastering") == "match"
    assert clamp("eq") == "eq"


def open_window_with_project(tmp_path, width):
    """A window on a real recording, so every step carries a state label."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(313).normal(0, 0.2, (rate, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    window.show()
    window.loaded("source", load_audio(source))
    for _ in range(4):
        app.processEvents()
    # A resize before the window is mapped is ignored offscreen.
    window.resize(width, 900)
    for _ in range(8):
        app.processEvents()
    return app, window


def bar_state(window):
    """Visible tab labels and whether they fit without scrolling."""
    bar = window.views.navigation
    visible = [index for index in range(bar.count()) if bar.isTabVisible(index)]
    labels = [bar.tabText(index) for index in visible]
    needed = sum(bar.tabSizeHint(index).width() for index in visible)
    return labels, needed <= bar.width()


def test_every_step_offers_a_short_title():
    """A short title is the last way the bar fits; a step without one cannot."""
    for step in STEPS:
        assert step.short, f"{step.key} has no short title"


def test_the_step_bar_keeps_every_step_visible_in_a_narrow_window(tmp_path):
    """Half-screening used to push Export off the end of the bar.

    Seven status labels need about 1085px, which a narrow window does not have.
    The bar now drops the state and keeps the titles rather than scrolling.
    """
    app, window = open_window_with_project(tmp_path, 945)
    try:
        labels, fits = bar_state(window)
        assert len(labels) == len(NAVIGATION)
        assert fits, f"the bar still scrolls at 945px: {labels}"
        assert any("Export" in label for label in labels)
    finally:
        window.project.mark_saved()
        window.close()
        app.processEvents()


def test_the_full_labels_return_when_the_window_is_wide_again(tmp_path):
    app, window = open_window_with_project(tmp_path, 945)
    try:
        narrow, _ = bar_state(window)
        window.resize(1600, 900)
        for _ in range(8):
            app.processEvents()
        wide, _ = bar_state(window)
        assert wide != narrow
        assert any("\u2014" in label for label in wide), wide
    finally:
        window.project.mark_saved()
        window.close()
        app.processEvents()


def test_narrowing_the_bar_never_loses_a_step_state(tmp_path):
    """The suffix is what goes; the state moves to the tooltip rather than away."""
    app, window = open_window_with_project(tmp_path, 945)
    try:
        bar = window.views.navigation
        visible = [index for index in range(bar.count()) if bar.isTabVisible(index)]
        assert not any("\u2014" in bar.tabText(index) for index in visible)
        keys = window.views.visibleSteps()
        states = dict(zip(keys, (bar.tabToolTip(i) for i in visible), strict=True))
        # Listen and Mark is not a processing stage, so it has no state to show.
        with_state = [key for key in keys if BY_KEY[key].stage is not None or key == "export"]
        assert all("\u2014" in states[key] for key in with_state), states
        assert any("skipped" in states[key] for key in with_state), states
        assert states["listen"] == BY_KEY["listen"].title
    finally:
        window.project.mark_saved()
        window.close()
        app.processEvents()


def test_the_window_refuses_to_go_below_the_narrow_layout_floor():
    """Below this the stacked layout has no room for the plots at all."""
    window = Window()
    try:
        assert window.minimumWidth() >= 800
        assert window.minimumWidth() <= STEP_BAR_NARROWEST + 200
    finally:
        window.close()


# The tightest label set, measured. The bar must fit inside the window floor.
STEP_BAR_NARROWEST = 620
