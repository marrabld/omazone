"""Explicit proposal/apply workflow, preserved calibration and correct stage A/B."""

import json
import threading
import time
from dataclasses import asdict

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtCore, QtWidgets

from omazone.engine import analyse
from omazone.gui import Window, load_audio
from omazone.loudness_target import TargetSettings, propose_target, target_parameters
from omazone.project import load_project, save_project


@pytest.fixture
def session(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    rate = 16000
    audio = np.random.default_rng(102).normal(0, 0.07, (rate * 2, 2))
    audio[:: rate // 10] += [0.7, -0.4]
    path = tmp_path / "mix.wav"
    sf.write(path, audio, rate, subtype="DOUBLE")
    window = Window()
    errors = []
    window.error = errors.append
    window.show()
    window.loaded("source", load_audio(path))
    window.loaded("reference", load_audio(path))

    def wait():
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None
                and window.output_view.targeting.job is None
                and window.export_view.review_job is None
                and not window.export_view.review_timer.isActive()
                and window.workspace.job is None
                and not window.workspace.timer.isActive()
                and window.stage_measurements.job is None
                and not window.stage_measurements.timer.isActive()
            ):
                break
            time.sleep(0.005)
        assert window.worker is None and window.output_view.targeting.job is None
        assert (
            window.export_view.review_job is None and not window.export_view.review_timer.isActive()
        )
        assert not errors

    window.process()
    wait()
    window.views.setStep("output")
    window.output_view.target_toggle.setChecked(True)
    wait()
    yield window, window.output_view.targeting, wait, app
    window.project.mark_saved()
    window.close()
    app.processEvents()


def choose(view, lufs, mode="gain-only", ceiling=-1):
    view.lufs.setValue(lufs)
    view.ceiling.setValue(ceiling)
    view.mode.setCurrentIndex(view.mode.findData(mode))


def test_calculation_is_worker_only_and_does_not_change_recipe_audio_or_audition(
    session, monkeypatch
):
    window, target, wait, app = session
    before = {key: asdict(value) for key, value in window.project.stages.items()}
    output, preview, calibration = window.output[0], window.preview, window.project.calibration
    config, position = window.project.config_key(), window.position
    threads = []

    def measured(*args, **kwargs):
        threads.append(QtCore.QThread.currentThread())
        return propose_target(*args, **kwargs)

    monkeypatch.setattr("omazone.loudness_target_view.propose_target", measured)
    choose(target, -18)
    target.calculate_button.click()
    wait()
    assert target.proposal.converged and target.apply_button.isEnabled()
    assert threads and all(thread is not app.thread() for thread in threads)
    assert {key: asdict(value) for key, value in window.project.stages.items()} == before
    assert window.output[0] is output and window.preview is preview
    assert window.project.calibration is calibration and window.project.config_key() == config
    assert window.position == position and not window.project.needs_render
    assert "Proposed output gain" in target.message.text()


@pytest.mark.parametrize(
    "mode,lufs,comparison", [("gain-only", -18, "output-gain"), ("limiter", -10, "limiter")]
)
def test_apply_uses_existing_stages_preserves_prefixes_and_verifies_real_render(
    session, mode, lufs, comparison
):
    window, target, wait, _ = session
    calibration, config = window.project.calibration, window.project.config_key()
    counts = dict(window.renderer.computations)
    choose(target, lufs, mode)
    target.calculate()
    wait()
    proposal = target.proposal
    assert proposal.converged
    target.apply_button.click()
    wait()
    assert target.proposal is None
    assert window.project.stages["output"].parameters["gain_db"] == proposal.gain_db
    assert not window.project.stages["output"].bypassed
    assert window.project.stages["limiter"].bypassed is (mode == "gain-only")
    assert window.project.calibration is calibration and window.project.config_key() == config
    for key in ("repair", "match", "eq", "filters", "dynamic-eq", "dynamics"):
        assert window.renderer.computations[key] == counts[key]
    assert window.audition_mode == comparison
    pair = window.comparison(comparison)
    assert pair.before is (
        window.chain_result.pre_output if mode == "gain-only" else window.chain_result.pre_limiter
    )
    assert pair.after is window.chain_result.output
    assert "meet the request" in target.actual.text()


def test_unmet_target_requires_extra_consent_and_keeps_the_original_request(session, monkeypatch):
    window, target, wait, _ = session
    choose(target, -10)
    target.calculate()
    wait()
    assert not target.proposal.converged
    before = {key: asdict(value) for key, value in window.project.stages.items()}
    output = window.output[0]
    calls = []

    def cancel(*args):
        calls.append(args)
        return QtWidgets.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", cancel)
    target.apply()
    assert calls and calls[0][-1] == QtWidgets.QMessageBox.StandardButton.Cancel
    assert "not the requested" in calls[0][2]
    assert window.output[0] is output
    assert {key: asdict(value) for key, value in window.project.stages.items()} == before
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", lambda *args: QtWidgets.QMessageBox.StandardButton.Yes
    )
    target.apply()
    wait()
    assert target.settings().target_lufs == -10
    assert window.project.view["loudness_target"]["target_lufs"] == -10
    assert "Request not met" in target.actual.text()
    assert window.project.stages["limiter"].bypassed


def test_applying_gain_only_bypasses_but_retains_existing_limiter_settings(session):
    window, target, wait, _ = session
    window.output_view.ceiling.setValue(-3)
    window.output_view.lookahead.setValue(7.5)
    window.render_saved_recipe()
    wait()
    stored = dict(window.project.stages["limiter"].parameters)
    assert not window.project.stages["limiter"].bypassed
    choose(target, -18, "gain-only")
    target.calculate()
    wait()
    assert target.proposal.converged and target.proposal.limiter is None
    target.apply()
    wait()
    assert window.project.stages["limiter"].bypassed
    assert window.project.stages["limiter"].parameters == stored


def test_requests_and_committed_settings_roundtrip_without_automatic_targeting(
    session, tmp_path, monkeypatch
):
    window, target, wait, _ = session
    choose(target, -18)
    target.calculate()
    wait()
    target.apply()
    wait()
    expected = window.output[0].copy()
    stages = {key: asdict(value) for key, value in window.project.stages.items()}
    window.sync_project()
    path = tmp_path / "target.omazone.json"
    save_project(path, window.project)
    assert load_project(path).view["loudness_target"] == target_parameters(TargetSettings(-18, -1))
    monkeypatch.setattr(
        "omazone.loudness_target_view.propose_target",
        lambda *args: pytest.fail("Loading/rendering silently recalculated a target"),
    )
    window.project.mark_saved()
    window.load_project_path(path)
    wait()
    assert target.settings() == TargetSettings(-18, -1)
    assert target.proposal is None and window.output is None
    assert {key: asdict(value) for key, value in window.project.stages.items()} == stages
    window.render_saved_recipe()
    wait()
    np.testing.assert_array_equal(window.output[0], expected)
    # Legacy requests are absent, not automatically enabled processors.
    data = json.loads(path.read_text())
    del data["view"]["loudness_target"]
    path.write_text(json.dumps(data))
    window.project.mark_saved()
    window.load_project_path(path)
    wait()
    assert target.settings() == TargetSettings()
    assert {key: asdict(value) for key, value in window.project.stages.items()} == stages


@pytest.mark.parametrize("change", ["request", "gain", "delivery", "cancel"])
def test_stale_or_cancelled_worker_cannot_restore_a_proposal_or_change_sound(
    session, monkeypatch, change
):
    window, target, wait, app = session
    entered, release = threading.Event(), threading.Event()
    output = window.output[0]

    def delayed(audio, rate, settings, delivery, limiter, baseline, cancelled, progress):
        entered.set()
        assert release.wait(10)
        # Ignore cancellation deliberately, so stale-result checks are tested too.
        return propose_target(audio, rate, settings, delivery, limiter, baseline)

    monkeypatch.setattr("omazone.loudness_target_view.propose_target", delayed)
    try:
        choose(target, -18)
        target.calculate()
        deadline = time.monotonic() + 5
        while not entered.is_set() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert entered.is_set()
        if change == "request":
            target.lufs.setValue(-16)
        elif change == "gain":
            window.output_view.gain.setValue(-6)
        elif change == "delivery":
            view = window.export_view
            view.sample_rate.setCurrentIndex(view.sample_rate.findData(44100))
        else:
            target.cancel_button.click()
        release.set()
        wait()
        assert target.proposal is None and not target.apply_button.isEnabled()
        if change == "gain":
            assert window.project.stages["output"].parameters["gain_db"] == -6
            assert window.output is None
        else:
            assert window.output[0] is output
    finally:
        release.set()


def test_converted_delivery_outcome_and_actual_written_file_are_distinguished(
    session, tmp_path, monkeypatch
):
    window, target, wait, _ = session
    view = window.export_view
    view.sample_rate.setCurrentIndex(view.sample_rate.findData(44100))
    wait()
    choose(target, -18)
    target.calculate()
    wait()
    assert target.proposal.measured.rate == 44100
    target.apply()
    wait()
    assert "44.1 kHz" in target.actual.text() and "meet the request" in target.actual.text()
    path = tmp_path / "target.wav"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    window.export()
    wait()
    assert sf.info(path).samplerate == 44100
    assert (
        "Last written file" in target.written.text() and "meet the request" in target.written.text()
    )


def test_export_only_attenuation_cannot_leave_a_false_written_target_success(session, tmp_path):
    from omazone.delivery import write

    window, target, wait, _ = session
    choose(target, -18)
    target.calculate()
    wait()
    target.apply()
    wait()
    assert "meet the request" in target.actual.text()
    settings = window.export_view.delivery_settings()
    report = write(tmp_path / "quieter.wav", window.output[0], 16000, settings, attenuation_db=-6)
    window.export_view.record_written(report, settings)
    window.output_view.draw()
    assert "meet the request" in target.actual.text()
    assert "Request not met" in target.written.text()


def test_loaded_request_precision_is_retained_until_that_field_is_edited(session):
    window, target, _, _ = session
    request = TargetSettings(-16.25, -1.25)
    window.project.view["loudness_target"] = target_parameters(request)
    target.restore()
    window.sync_project()
    assert target.settings() == request
    assert window.project.view["loudness_target"] == target_parameters(request)
    target.lufs.setValue(-16.2)
    assert target.settings().target_lufs == -16.2
    assert target.settings().ceiling_db == -1.25


def test_unknown_saved_target_policy_is_rejected_without_replacing_work(session, tmp_path):
    window, _, _, _ = session
    window.sync_project()
    path = tmp_path / "unsupported.omazone.json"
    save_project(path, window.project)
    data = json.loads(path.read_text())
    data["view"]["loudness_target"]["kind"] = "future"
    path.write_text(json.dumps(data))
    original = window.output[0]
    with pytest.raises(ValueError, match="Unsupported loudness-target"):
        load_project(path)
    assert window.output[0] is original


def test_undefined_baseline_produces_no_proposal(session):
    window, target, wait, _ = session
    audio = window.source[0] * 1e-6
    window.loaded("source", (audio, 16000, analyse(audio, 16000), "Below gate"))
    window.project.stages["match"].bypassed = True
    window.render_saved_recipe()
    wait()
    window.views.setStep("output")
    target.calculate()
    wait()
    assert target.proposal is None and not target.apply_button.isEnabled()
    assert "400 ms" in target.message.text()


@pytest.mark.parametrize("width,height", [(820, 560), (1280, 800)])
def test_target_controls_scroll_without_crushing_plots_or_actions(session, width, height):
    window, target, wait, app = session
    window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("spectrum"))
    window.resize(width, height)
    wait()
    for _ in range(8):
        app.processEvents()
    assert window.width() == width and window.height() == height
    assert window.tool_scroll.isAncestorOf(target)
    assert not window.tool_scroll.isAncestorOf(window.output_view.render_button)
    assert window.tool_scroll.horizontalScrollBar().maximum() == 0
    assert window.workspace.output_canvas.height() >= 100
