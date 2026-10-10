"""The milestone acceptance journeys from docs/workflow-consolidation.md.

Each test here maps to one numbered journey, so a failure names the promise that
broke rather than an incidental detail. Journey 5 (unsaved-project guards) lives
in tests/test_unsaved_project.py because it is about project safety rather than
the processing workflow.
"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtWidgets

from omazone.comparison import Side
from omazone.engine import audition_pair
from omazone.gui import Window, load_audio
from omazone.pipeline import ChainRenderer
from omazone.project import load_project
from omazone.waveform import SampleRegion
from omazone.workflow_status import RenderState

RATE = 16000


@pytest.fixture
def studio(tmp_path):
    """A window with a mix and a reference on disk, and a clock to wait on."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    seconds = np.arange(RATE * 4) / RATE
    clean = (0.9 * np.sin(2 * np.pi * 440 * seconds))[:, None]
    clipped = np.clip(clean, -0.45, 0.6)
    source = tmp_path / "mix.wav"
    reference = tmp_path / "reference.wav"
    sf.write(source, clipped, RATE, subtype="DOUBLE")
    sf.write(reference, clean, RATE, subtype="DOUBLE")
    window.show()
    window.loaded("source", load_audio(source))

    def wait():
        deadline = time.monotonic() + 20
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None, "a job never finished"

    yield window, wait, app, tmp_path, clipped, clean
    if window.worker is not None:
        window.worker.wait()
    window.close()
    # The unsaved guard answers on the next turn, so let it run before returning.
    app.processEvents()


def test_journey_one_no_reference_skip_the_optional_stages_and_export(studio, monkeypatch):
    """Load a mix, mark a passage, skip the optional stages, set gain, export."""
    window, wait, _, tmp_path, _, _ = studio
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "a.wav"), "")
    )

    window.waveform.set_selection(SampleRegion(2000, 12000))
    window.region_name.setText("Verse")
    window.name_region_button.click()
    assert [region.name for region in window.project.regions] == ["Verse"]

    # Skip the optional stages that are not already skipped. Repair and Match
    # start on; Manual EQ and Dynamics start off.
    skipped = []
    for stage in ("repair", "match", "eq", "dynamics"):
        if not window.project.stages[stage].bypassed:
            window.views.setStep(stage)
            assert window.skip_button.text() == f"Skip {window.views.currentStep().title()}"
            window.skip_button.click()
            skipped.append(stage)
    assert skipped == ["repair", "match"]
    assert all(
        window.project.stages[stage].bypassed for stage in ("repair", "match", "eq", "dynamics")
    )

    while window.views.currentStep() != "output":
        window.go_continue()

    window.output_view.enabled.setChecked(True)
    window.output_view.gain.setValue(-2.5)
    window.output_view.render_button.click()
    wait()
    window.go_continue()
    assert window.views.currentStep() == "export"
    assert window.export_view.export_button.isEnabled()

    window.export_view.export_button.click()
    wait()
    written, rate = sf.read(tmp_path / "a.wav", always_2d=True, dtype="float32")
    assert rate == RATE
    np.testing.assert_allclose(written, window.output[0], atol=1e-6)


def test_journey_one_every_disabled_action_explains_itself(studio):
    """A disabled primary action carries a visible reason, not only a tooltip."""
    window, _, _, _, _, _ = studio
    window.views.setStep("match")
    assert not window.process_button.isEnabled()
    reason = window.views.action_label.text()
    assert reason and reason.strip()
    assert "reference" in reason.lower(), reason

    window.views.setStep("export")
    assert not window.export_view.export_button.isEnabled()
    assert window.export_view.route.text(), "a blocked export must say where to go"


def test_journey_two_every_stage_compares_its_named_prefix(studio):
    """Each comparison plays exactly the ChainResult prefixes it names."""
    window, wait, _, tmp_path, clipped, _ = studio
    reference = tmp_path / "reference.wav"

    # Repair works on a selected passage, as a learner would mark one first.
    window.waveform.set_selection(SampleRegion(0, len(clipped)))
    window.clipping_inspector.find_peaks()
    wait()
    window.clipping_inspector.check_shown(True)
    window.clipping_inspector.repair()
    wait()
    assert window.project.repairs

    window.loaded("reference", load_audio(reference))
    window.process()
    wait()
    window.manual_eq_view.enabled.setChecked(True)
    window.manual_eq_view.add_band(2100, -3)
    window.compressor_view.enabled.setChecked(True)
    window.compressor_view.threshold.setValue(-20)
    window.output_view.enabled.setChecked(True)
    window.output_view.gain.setValue(-2)
    window.views.setStep("output")
    window.output_view.render_button.click()
    wait()

    result = window.chain_result
    assert result is not None
    cases = (
        ("eq", result.matched, result.equalized, window.eq_preview),
        ("dynamics", result.equalized, result.pre_output, window.dynamics_preview),
        ("output-gain", result.pre_output, result.output, window.output_preview),
    )
    for key, before, after, preview in cases:
        window.views.setStep(key)
        window.apply_comparison(key, Side.AFTER)
        shown_before, shown_after, actual_rate, _ = window.workspace.pairs()
        assert actual_rate == RATE
        np.testing.assert_array_equal(shown_before, before)
        np.testing.assert_array_equal(shown_after, after)
        expected = audition_pair(before, after)
        assert preview is not None
        np.testing.assert_array_equal(preview[0], expected[0])
        np.testing.assert_array_equal(preview[1], expected[1])
        assert window.ab_button.isEnabled()


def test_journey_three_an_upstream_edit_keeps_settings_but_stales_the_rest(studio):
    """Editing an earlier stage preserves later settings and re-derives freshness."""
    window, wait, _, tmp_path, clipped, _ = studio
    # Repair works on a selected passage, as a learner would mark one first.
    window.waveform.set_selection(SampleRegion(0, len(clipped)))
    window.clipping_inspector.find_peaks()
    wait()
    window.clipping_inspector.check_shown(True)
    window.clipping_inspector.repair()
    wait()
    window.loaded("reference", load_audio(tmp_path / "reference.wav"))
    window.process()
    wait()
    window.manual_eq_view.enabled.setChecked(True)
    window.manual_eq_view.add_band(2100, -3)
    window.compressor_view.enabled.setChecked(True)
    window.output_view.enabled.setChecked(True)
    window.output_view.gain.setValue(-2)
    window.views.setStep("output")
    window.output_view.render_button.click()
    wait()

    assert window.export_button.isEnabled()
    assert "Output RMS" in window.meters.text()
    assert window.compressor_view.listen_button.isEnabled()

    # Move EQ. Later settings survive, downstream work goes stale, export closes.
    window.views.setStep("manual_eq_view")
    window.manual_eq_view.gain.setValue(-5)

    assert window.project.stages["dynamics"].bypassed is False
    assert window.compressor_view.threshold.value() == -18
    assert window.project.stages["output"].bypassed is False
    status = window.workflow_status()
    assert status.stages["eq"].state is RenderState.NEEDS_RENDER
    assert status.stages["dynamics"].state is RenderState.NEEDS_RENDER
    assert status.stages["output"].state is RenderState.NEEDS_RENDER
    assert not status.export_available
    assert not window.export_button.isEnabled()
    assert not window.compressor_view.listen_button.isEnabled()
    assert window.meters.text() == "Render to update measurements."

    window.views.setStep("eq")
    window.manual_eq_view.render_button.click()
    wait()
    assert window.workflow_status().export_available
    assert window.export_button.isEnabled()


def test_journey_four_save_reopen_compare_and_export_matches_the_recipe(studio, monkeypatch):
    """Save, reopen in a fresh window, rerender without relearning, export."""
    window, wait, _, tmp_path, clipped, _ = studio
    # Repair works on a selected passage, as a learner would mark one first.
    window.waveform.set_selection(SampleRegion(0, len(clipped)))
    window.clipping_inspector.find_peaks()
    wait()
    window.clipping_inspector.check_shown(True)
    window.clipping_inspector.repair()
    wait()
    window.loaded("reference", load_audio(tmp_path / "reference.wav"))
    window.process()
    wait()
    window.manual_eq_view.enabled.setChecked(True)
    window.manual_eq_view.add_band(2100, -3)
    window.compressor_view.enabled.setChecked(True)
    window.output_view.enabled.setChecked(True)
    window.output_view.gain.setValue(-2)
    window.views.setStep("output")
    window.output_view.render_button.click()
    wait()

    project_path = tmp_path / "session.omazone.json"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *a: (str(project_path), "")
    )
    window.waveform.set_selection(SampleRegion(4000, 16000))
    window.loop_selection.setChecked(True)
    window.save_current_project()
    wait()
    learned = window.project.calibration.whole.coefficients.copy()

    reopened = Window()
    try:
        errors = []
        reopened.error = errors.append
        reopened.show()
        reopened.open_project_path(project_path)
        wait_for(reopened)
        assert not errors

        # Session context came back with the project.
        assert reopened.project.regions or reopened.waveform.selection is not None
        assert reopened.transport.loop

        # Rerendering must not silently relearn the target.
        reopened.views.setStep("output")
        reopened.output_view.render_button.click()
        wait_for(reopened)
        assert not errors
        np.testing.assert_array_equal(reopened.project.calibration.whole.coefficients, learned)

        reopened.views.setStep("eq")
        reopened.apply_comparison("eq", Side.AFTER)
        assert reopened.ab_button.isEnabled()

        reopened.views.setStep("export")
        target = tmp_path / "reopened.wav"
        monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a: (str(target), ""))
        reopened.export_view.export_button.click()
        wait_for(reopened)

        written, rate = sf.read(target, always_2d=True, dtype="float32")
        assert rate == RATE
        assert sf.info(target).subtype == "FLOAT"
        assert written.shape[1] == clipped.shape[1]

        recipe = load_project(project_path)
        direct = ChainRenderer(clipped, RATE, None).render(recipe).output
        assert np.array_equal(written, direct.astype(np.float32))
    finally:
        reopened.close()
        QtWidgets.QApplication.instance().processEvents()


def wait_for(window):
    app = QtWidgets.QApplication.instance()
    deadline = time.monotonic() + 20
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert window.worker is None


def test_journey_two_repair_is_optional_and_changes_the_audio(studio):
    """Repair is a real stage: applying it alters the chain it feeds."""
    window, wait, _, _, clipped, _ = studio
    # Repair works on a selected passage, as a learner would mark one first.
    window.waveform.set_selection(SampleRegion(0, len(clipped)))
    window.clipping_inspector.find_peaks()
    wait()
    window.clipping_inspector.check_shown(True)
    window.clipping_inspector.repair()
    wait()
    repaired = window.repair_result
    assert repaired is not None
    assert not np.array_equal(repaired.audio, clipped)
    assert window.processing_source()[0] is repaired.audio
    # The preview is level matched for listening, the chain itself is not.
    preview = window.repair_preview
    assert preview is not None
    np.testing.assert_array_equal(preview[0], audition_pair(clipped, repaired.audio)[0])


def test_every_optional_stage_can_be_skipped_and_the_chain_still_exports(studio, monkeypatch):
    """Optional stages are optional: skipping all of them still yields a file."""
    window, wait, _, tmp_path, _, _ = studio
    for stage in ("repair", "match"):
        window.views.setStep(stage)
        window.skip_button.click()
    assert all(
        window.project.stages[stage].bypassed for stage in ("repair", "match", "eq", "dynamics")
    )
    window.output_view.enabled.setChecked(True)
    window.output_view.render_button.click()
    wait()
    window.views.setStep("export")
    assert window.export_view.export_button.isEnabled()

    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "flat.wav"), "")
    )
    window.export_view.export_button.click()
    wait()
    written, rate = sf.read(tmp_path / "flat.wav", always_2d=True, dtype="float32")
    assert rate == RATE
    assert not np.allclose(written, 0)


def test_repair_is_not_applied_without_accepted_peaks(studio):
    """Skipping or declining repair leaves the original untouched."""
    window, wait, _, _, clipped, _ = studio
    # Repair works on a selected passage, as a learner would mark one first.
    window.waveform.set_selection(SampleRegion(0, len(clipped)))
    window.clipping_inspector.find_peaks()
    wait()
    window.clipping_inspector.check_shown(False)
    window.clipping_inspector.repair()
    wait()
    assert window.repair_result is None
    np.testing.assert_array_equal(window.source[0], clipped)
    assert window.processing_source()[0] is window.source[0]
    assert window.workflow_status().stages["repair"].state is RenderState.SKIPPED
