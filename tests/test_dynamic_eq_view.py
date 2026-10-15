"""Dynamic EQ user journeys, precise recipes, stale state and isolated audition."""

import copy
import time

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtWidgets

from omazone.dynamic_eq import DynamicEQSettings, dynamic_eq_parameters
from omazone.gui import Window, load_audio
from omazone.playback import AuditionProcessor
from omazone.waveform import SampleRegion
from omazone.workflow_steps import WORKFLOW


@pytest.fixture
def session(tmp_path, monkeypatch):
    import sounddevice as sd

    class Stream:
        active = True
        latency = 0.05

        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    monkeypatch.setattr(sd, "OutputStream", Stream)
    monkeypatch.setattr("omazone.gui.AuditionProcessor", lambda: AuditionProcessor(transition_ms=0))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    t = np.arange(rate * 2) / rate
    left = np.where(t < 0.5, 0.01, 0.5) * np.sin(2 * np.pi * 2400 * t)
    audio = np.column_stack((left, -0.6 * left))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    window.loaded("source", load_audio(source))
    window.show()

    def wait():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.processEvents()
            meter = window.stage_measurements
            if window.worker is None and meter.job is None and not meter.timer.isActive():
                break
            time.sleep(0.005)
        assert window.worker is None and not errors

    yield window, window.dynamic_eq_view, wait, app
    if window.worker is not None:
        window.worker.wait()
        app.processEvents()
    window.project.mark_saved()
    window.close()
    app.processEvents()


def test_dynamic_eq_is_secondary_and_old_compression_defaults_are_untouched(session):
    window, view, _, _ = session
    assert window.project.stages["dynamic-eq"].bypassed and not view.enabled.isChecked()
    assert tuple(window.views.visibleSteps()) == WORKFLOW
    recipe = copy.deepcopy(window.project.stages["dynamics"])
    window.views.setStep("dynamics")
    window.dynamic_eq_link.click()
    assert window.views.currentWidget() is view
    assert window.workspace.plot_stack.currentWidget() is view.canvas
    assert window.project.stages["dynamics"] == recipe


def test_render_scope_preview_envelope_cursor_and_export_are_correct(
    session, tmp_path, monkeypatch
):
    window, view, wait, _ = session
    window.views.setStep("dynamic-eq")
    window.waveform.set_selection(SampleRegion(10000, 20000))
    view.enabled.setChecked(True)
    view.render_button.click()
    wait()
    chain = window.chain_result
    assert chain.dynamic_eq.max_reduction_db == 6
    assert chain.output is chain.dynamic_equalized
    assert window.audition_mode == "dynamic-eq"
    assert window.workspace.pairs()[0] is chain.filtered
    assert window.workspace.pairs()[1] is chain.dynamic_equalized
    assert window.stage_measurements.request.comparison_key == "dynamic-eq"
    assert "Selected passage" in window.stage_measurements.scope.text()
    assert "Maximum rendered cut" in view.summary.text()
    np.testing.assert_array_equal(
        view.canvas.reduction_plot.listDataItems()[0].yData, chain.dynamic_eq.reduction_db
    )
    window.position = 12000
    window.update_transport()
    assert view.canvas.cursor.value() == 0.75
    view.listen_button.click()
    block = np.empty((128, 2), dtype=np.float32)
    window.stream.callback(block, 128, None, None)
    np.testing.assert_array_equal(block, window.dynamic_eq_preview[0][10000:10128])
    window.toggle_ab()
    window.stream.callback(block, 128, None, None)
    np.testing.assert_array_equal(block, window.dynamic_eq_preview[1][10128:10256])
    window.stop()
    exported = tmp_path / "master.wav"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(exported), ""))
    window.export()
    wait()
    saved, rate = sf.read(exported, always_2d=True)
    assert rate == 16000 and len(saved) == len(window.source[0])
    np.testing.assert_allclose(saved, chain.output, atol=3e-8)
    window.views.setStep("export")
    assert any(
        "Dynamic EQ" in window.export_view.chain.item(index).text()
        for index in range(window.export_view.chain.count())
    )


def test_edits_preserve_upstream_buffers_and_invalidate_downstream_readings(session):
    window, view, wait, _ = session
    window.manual_eq_view.add_band(1800, -2)
    window.filter_view.add_filter(80)
    window.views.setStep("dynamic-eq")
    view.enabled.setChecked(True)
    window.compressor_view.enabled.setChecked(True)
    view.render_button.click()
    wait()
    before = window.dynamic_eq_before
    eq_preview = window.eq_preview
    filters_preview = window.filters_preview
    recipes = copy.deepcopy(window.project.stages)
    view.controls["attack_ms"].setValue(30)
    assert window.dynamic_eq_before is before and window.dynamic_eq_after is None
    assert window.eq_preview is eq_preview and window.filters_preview is filters_preview
    assert window.dynamics_before is None and window.output is None
    for key in ("eq", "filters", "dynamics"):
        assert window.project.stages[key] == recipes[key]
    assert window.stage_measurements.request.after is None
    assert not view.canvas.reduction_plot.listDataItems()
    view.render_button.click()
    wait()
    assert window.chain_result.filtered is before
    assert window.renderer.computations["eq"] == 1 and window.renderer.computations["filters"] == 1
    assert window.renderer.computations["dynamic-eq"] == 2


def test_saved_precision_survives_other_control_edits_and_reopen(session, tmp_path, monkeypatch):
    window, view, wait, _ = session
    settings = DynamicEQSettings(frequency=2345.678912, q=2.123456789)
    window.project.stages["dynamic-eq"].parameters = dynamic_eq_parameters(settings)
    view.restore()
    window.views.setStep("dynamic-eq")
    view.controls["attack_ms"].setValue(25)
    assert view.value.frequency == settings.frequency and view.value.q == settings.q
    file = tmp_path / "saved.omazone.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(file), ""))
    window.save_current_project()
    wait()
    window.new_project()
    window.open_project_path(file)
    wait()
    assert view.value.frequency == settings.frequency and view.value.q == settings.q
    assert view.value.attack_ms == 25
    assert not window.project.stages["dynamic-eq"].bypassed


def test_malformed_recipe_is_not_replaced_until_explicit_reset(session):
    window, view, _, _ = session
    unknown = {"kind": "dynamic-eq-v99", "keep": "future"}
    window.project.stages["dynamic-eq"].parameters = unknown.copy()
    view.restore()
    assert view.invalid_recipe
    assert all(not control.isEnabled() for control in view.controls.values())
    view.controls["attack_ms"].setValue(100)
    assert window.project.stages["dynamic-eq"].parameters == unknown
    assert not view.canvas.response_plot.listDataItems()
    view.reset_band()
    assert view.invalid_recipe is None and window.project.stages["dynamic-eq"].bypassed
    assert window.project.stages["dynamic-eq"].parameters["kind"] == "dynamic-eq-v1"


def test_navigation_does_not_skip_unrendered_dynamic_eq_with_compressor_bypassed(session):
    window, view, _, _ = session
    view.enabled.setChecked(True)
    window.views.setStep("dynamics")
    assert not window.current_step_status(window.workflow_status()).can_continue
    assert not window.continue_button.isEnabled()
    assert window.project.stages["dynamics"].bypassed


def test_compressor_edits_keep_current_dynamic_eq_audio_and_gain_history(session):
    window, view, wait, _ = session
    window.views.setStep("dynamic-eq")
    view.enabled.setChecked(True)
    view.render_button.click()
    wait()
    result = window.dynamic_eq_result
    preview = window.dynamic_eq_preview
    window.compressor_view.threshold.setValue(-30)
    assert window.chain_result is None
    assert window.dynamic_eq_result is result and window.dynamic_eq_preview is preview
    view.draw()
    np.testing.assert_array_equal(
        view.canvas.reduction_plot.listDataItems()[0].yData, result.reduction_db
    )


def test_bypassing_band_keeps_recipe_and_renders_exact_upstream_audio(session):
    window, view, wait, _ = session
    window.views.setStep("dynamic-eq")
    view.controls["q"].setValue(3)
    view.render_button.click()
    wait()
    expected = window.output[0].copy()
    recipe = copy.deepcopy(window.project.stages["dynamic-eq"].parameters)
    view.enabled.setChecked(False)
    assert window.project.stages["dynamic-eq"].parameters == recipe
    assert window.dynamic_eq_result is None and window.dynamic_eq_preview is None
    view.render_button.click()
    wait()
    np.testing.assert_array_equal(window.output[0], window.source[0])
    assert "skipped" in view.canvas.reduction_plot.plotItem.titleLabel.text.lower()
    view.enabled.setChecked(True)
    view.render_button.click()
    wait()
    np.testing.assert_array_equal(window.output[0], expected)


@pytest.mark.parametrize("width,height", [(820, 560), (900, 768), (1280, 800)])
def test_dynamic_eq_layout_preserves_readable_graphs_and_reachable_actions(session, width, height):
    window, view, _, app = session
    window.views.setStep("dynamic-eq")
    window.resize(width, height)
    for _ in range(8):
        app.processEvents()
    assert window.width() == width and window.height() == height
    assert window.views.navigationFits()
    assert window.tool_scroll.horizontalScrollBar().maximum() == 0
    assert not window.tool_scroll.isAncestorOf(view.render_button)
    assert view.render_button.isVisible()
    for plot in (view.canvas.response_plot, view.canvas.reduction_plot):
        assert plot.getViewBox().height() >= 90
