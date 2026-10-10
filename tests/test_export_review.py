"""The Export step: review the chain, then write exactly what was rendered."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtWidgets

from omazone.gui import Window, load_audio
from omazone.pipeline import ChainRenderer
from omazone.project import load_project
from omazone.workflow_status import AnalysisState


@pytest.fixture
def session(tmp_path):
    """A window with a real file loaded, ready to render and export."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(90210).normal(0, 0.2, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    window.show()
    window.loaded("source", load_audio(source))

    def wait():
        deadline = time.monotonic() + 20
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    yield window, wait, tmp_path, audio, rate, app
    if window.worker is not None:
        window.worker.wait()
    window.close()
    # The unsaved guard answers on the next event loop turn, so spin it once or
    # every window in this file stays alive for the rest of the session.
    app.processEvents()


def wait_for(window):
    app = QtWidgets.QApplication.instance()
    deadline = time.monotonic() + 20
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert window.worker is None


def render_output(window, wait=None):
    wait = wait or (lambda: wait_for(window))
    window.views.setStep("output")
    window.output_view.enabled.setChecked(True)
    window.output_view.gain.setValue(-3)
    window.output_view.render_button.click()
    wait()


def rows(window):
    return [
        window.export_view.chain.item(i).text() for i in range(window.export_view.chain.count())
    ]


@pytest.mark.parametrize(
    ("match", "eq", "dynamics", "expected"),
    (
        (
            False,
            False,
            False,
            ["Repair: skipped", "Match: skipped", "Manual EQ: skipped", "Dynamics: skipped"],
        ),
        (
            True,
            False,
            False,
            ["Repair: skipped", "Match: ready", "Manual EQ: skipped", "Dynamics: skipped"],
        ),
        (
            False,
            True,
            False,
            ["Repair: skipped", "Match: skipped", "Manual EQ: ready", "Dynamics: skipped"],
        ),
        (
            False,
            False,
            True,
            ["Repair: skipped", "Match: skipped", "Manual EQ: skipped", "Dynamics: ready"],
        ),
    ),
)
def test_the_chain_is_listed_in_processing_order(session, match, eq, dynamics, expected):
    """Skipped stages stay listed, so the learner can see what was left out."""
    window, wait, tmp_path, _, rate, _app = session
    if eq:
        window.manual_eq_view.enabled.setChecked(True)
        window.manual_eq_view.add_band(1800, -4)
    if dynamics:
        window.compressor_view.enabled.setChecked(True)
    if match:
        reference = np.random.default_rng(1).normal(0, 0.2, (32000, 2))
        path = tmp_path / "reference.wav"
        sf.write(path, reference, rate, subtype="DOUBLE")
        window.loaded("reference", load_audio(path))
        window.process()
        wait()
    render_output(window, wait)
    window.views.setStep("export")
    found = rows(window)
    assert found[:-1] == expected
    assert found[-1] == "Output gain: ready"
    assert [text.split(":")[0] for text in found] == [
        "Repair",
        "Match",
        "Manual EQ",
        "Dynamics",
        "Output gain",
    ]


def test_export_is_the_final_step_and_cannot_be_skipped(session):
    window, _, _, _, _, _ = session
    assert window.views.visibleSteps()[-1] == "export"
    window.views.setStep("export")
    assert not window.skip_button.isVisible()


def test_a_stale_chain_disables_export_and_routes_to_the_blocking_step(session):
    window, wait, _, _, _, _ = session
    window.manual_eq_view.enabled.setChecked(True)
    window.views.setStep("export")
    assert not window.export_view.export_button.isEnabled()
    assert window.export_view.route.text() == "Go to Manual EQ"
    assert window.export_view.summary.text() == "Add a band to use this step, or skip it."

    window.export_view.route.click()
    assert window.views.currentStep() == "eq"

    # Once the blocker is gone the route disappears and points at pending audio.
    window.manual_eq_view.add_band(1800, -4)
    window.views.setStep("export")
    assert window.export_view.route.text() == "Go to Manual EQ"
    window.views.setStep("eq")
    window.manual_eq_view.render_button.click()
    wait()
    window.views.setStep("export")
    # Rendering one stage renders the chain, so nothing is left to route to.
    assert window.export_view.route.text() == ""
    assert window.export_view.export_button.isEnabled()


def test_export_writes_the_chain_render_and_never_a_preview(session, monkeypatch):
    window, wait, tmp_path, audio, rate, _app = session
    window.manual_eq_view.enabled.setChecked(True)
    window.manual_eq_view.add_band(1800, -4)
    window.compressor_view.enabled.setChecked(True)
    window.compressor_view.threshold.setValue(-20)
    render_output(window, wait)
    window.views.setStep("export")
    assert window.export_view.export_button.isEnabled()

    preview = window.eq_preview
    chain = window.output[0].copy()
    assert preview is not None
    # The stage preview is level-matched, so it must not be what gets written.
    assert not np.array_equal(preview[1], chain)

    target = tmp_path / "final.wav"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    window.export_view.export_button.click()
    wait()
    written, written_rate = sf.read(target, always_2d=True, dtype="float32")
    assert written_rate == rate
    assert sf.info(target).subtype == "FLOAT"
    assert written.shape == chain.shape
    np.testing.assert_array_equal(written, chain.astype(np.float32))


def test_exported_samples_match_a_direct_render_of_the_reopened_recipe(session, monkeypatch):
    """The strongest check #53 asks for: the file equals the recipe, exactly."""
    window, wait, tmp_path, audio, rate, _app = session
    window.manual_eq_view.enabled.setChecked(True)
    window.manual_eq_view.add_band(2100, -3.5)
    window.compressor_view.enabled.setChecked(True)
    render_output(window, wait)

    project_path = tmp_path / "session.omazone.json"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *a: (str(project_path), "")
    )
    window.save_current_project()
    wait()

    reopened = Window()
    try:
        errors = []
        reopened.error = errors.append
        reopened.show()
        reopened.open_project_path(project_path)
        wait_for(reopened)
        render_output(reopened)
        assert not errors
        reopened.views.setStep("export")

        target = tmp_path / "reopened.wav"
        monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a: (str(target), ""))
        reopened.export_view.export_button.click()
        wait_for(reopened)
        assert not errors
        written, written_rate = sf.read(target, always_2d=True, dtype="float32")
        assert written_rate == rate

        recipe = load_project(project_path)
        direct = ChainRenderer(audio, rate, None).render(recipe).output
        assert np.array_equal(written, direct.astype(np.float32)), np.abs(
            written - direct.astype(np.float32)
        ).max()
    finally:
        reopened.close()
        QtWidgets.QApplication.instance().processEvents()


def test_retained_analysis_is_shown_separately_from_render_state(session):
    window, wait, tmp_path, _, rate, _app = session
    reference = np.random.default_rng(3).normal(0, 0.2, (32000, 2))
    path = tmp_path / "reference.wav"
    sf.write(path, reference, rate, subtype="DOUBLE")
    window.loaded("reference", load_audio(path))
    window.process()
    wait()
    render_output(window, wait)
    window.views.setStep("export")
    assert window.export_view.analysis.text()

    # Changing the input leaves the saved curve valid but no longer learned here.
    # Bypassing Repair changes the input, so the saved curve stays valid but was
    # not learned from what is in the chain now.
    window.set_stage_bypass("repair", True)
    render_output(window, wait)
    assert window.workflow_status().matching_analysis is AnalysisState.RETAINED
    window.views.setStep("export")
    assert "retained" in window.export_view.analysis.text().lower()
    assert window.export_view.export_button.isEnabled(), "retained analysis still allows export"


def test_blocking_stage_is_none_once_the_chain_is_current(session):
    """A ready project must not read as blocked."""
    window, wait, _, _, _, _ = session
    window.views.setStep("export")
    assert not window.export_view.export_button.isEnabled()
    assert window.workflow_status().blocking_stage == "output", "nothing rendered yet"

    window.output_view.enabled.setChecked(True)
    window.output_view.gain.setValue(-3)
    window.output_view.render_button.click()
    wait()
    status = window.workflow_status()
    assert status.export_available
    assert status.blocking_stage is None, "a ready project is not blocked"
    window.views.setStep("export")
    assert window.export_view.route.isHidden()


def test_blocking_stage_is_none_when_no_recording_is_loaded():
    from omazone.workflow_status import derive_workflow_status

    window = Window()
    try:
        window.show()
        status = derive_workflow_status(window.project, source_loaded=False)
        assert not status.export_available
        assert status.blocking_stage is None, "there is no step to route to"
        assert status.render_action == "Load"
    finally:
        window.close()
        QtWidgets.QApplication.instance().processEvents()


def test_every_blocking_reason_offers_somewhere_to_go(session):
    """Wherever export is unavailable, the panel must offer a way forward."""
    window, wait, _, _, _, _ = session
    for enable_band in (False, True):
        window.manual_eq_view.enabled.setChecked(True)
        if enable_band:
            window.manual_eq_view.add_band(1800, -4)
        window.views.setStep("export")
        assert not window.export_view.export_button.isEnabled()
        assert not window.export_view.route.isHidden(), enable_band
        assert window.export_view.route.text()
    window.views.setStep("eq")
    window.manual_eq_view.render_button.click()
    wait()
    window.views.setStep("export")
    # Rendering any one stage renders the chain, so nothing is left to route to.
    assert window.export_view.route.isHidden()
    assert window.export_view.route.text() == ""
    assert window.export_view.export_button.isEnabled()


def test_no_recording_offers_the_loader_rather_than_a_dead_end():
    """With nothing loaded there is no step to visit, so the route must load."""
    window = Window()
    try:
        window.show()
        window.views.setStep("export")
        assert not window.export_view.export_button.isEnabled()
        assert not window.export_view.route.isHidden()
        assert window.export_view.route.text() == "Load a mix…"
        assert window.export_view.route_target == ("load", "Load")
        assert window.export_view.summary.text() == "Load a recording."
    finally:
        window.close()
        QtWidgets.QApplication.instance().processEvents()
