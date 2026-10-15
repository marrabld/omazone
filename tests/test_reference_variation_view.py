"""Real reference loading/capture and measured overlay, with no audio device."""

import time

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtCore, QtWidgets
from scipy import signal

from omazone.gui import Window, load_audio
from omazone.waveform import SampleRegion


@pytest.fixture
def session(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    rate = 16000
    audio = np.random.default_rng(102).normal(0, 0.08, (rate * 8, 2))
    reference = audio.copy()
    reference[rate * 4 :] = signal.sosfilt(
        signal.butter(2, 800, fs=rate, output="sos"), reference[rate * 4 :], axis=0
    )
    source, refpath = tmp_path / "mix.wav", tmp_path / "reference.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    sf.write(refpath, reference, rate, subtype="DOUBLE")
    window = Window()
    window.show()
    errors = []
    window.error = errors.append
    window.loaded("source", load_audio(source))

    def wait():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None
                and window.workspace.job is None
                and not window.workspace.timer.isActive()
                and window.stage_measurements.job is None
                and not window.stage_measurements.timer.isActive()
            ):
                break
            time.sleep(0.005)
        assert window.worker is None and window.workspace.job is None and not errors

    yield window, window.reference_variation, refpath, wait, app
    if window.worker is not None:
        window.worker.wait()
        app.processEvents()
    window.project.mark_saved()
    window.close()
    app.processEvents()


def load_reference(window, path, wait):
    window.views.setStep("match")
    window.load_audio_path("reference", path)
    wait()


def test_reference_loading_analyzes_in_worker_and_displays_measured_density(session, monkeypatch):
    from omazone.reference_variation import analyse_variation

    window, view, path, wait, app = session
    threads = []

    def measured(*args, **kwargs):
        threads.append(QtCore.QThread.currentThread())
        return analyse_variation(*args, **kwargs)

    monkeypatch.setattr("omazone.gui.analyse_variation", measured)
    load_reference(window, path, wait)
    assert threads and all(thread is not app.thread() for thread in threads)
    profile = window.project.reference_target
    assert profile.variation.available
    assert view.image is not None and np.max(view.image.image[..., 3]) > 0
    assert "1 reference passage" in view.note.text()
    assert "8/8" in view.note.text() and "not confidence" in view.note.text()
    assert "band-averaged" in window.spectrum_plot.plotItem.titleLabel.text
    assert len(window.spectrum_plot.listDataItems()) == 5  # Bounds, median, input and mean.
    named = {item.name(): item for item in window.spectrum_plot.listDataItems() if item.name()}
    assert named["Reference median"].opts["pen"].style() == QtCore.Qt.PenStyle.DashLine
    assert named["Reference mean"].opts["pen"].style() == QtCore.Qt.PenStyle.DotLine
    # Image bounds must not force a blank 120 dB plot around the actual curves.
    assert window.spectrum_plot.viewRange()[1][1] < -5


def test_overlay_toggle_does_not_reanalyse_or_change_recipe_calibration_or_output(
    session, monkeypatch
):
    window, view, path, wait, _ = session
    load_reference(window, path, wait)
    window.process()
    wait()
    config, input_key, revision = (
        window.project.config_key(),
        window.project.input_key(),
        window.project.revision,
    )
    coefficients = window.project.calibration.whole.coefficients.copy()
    output = window.output[0].copy()
    preview = window.preview

    def unexpected(*args):
        raise AssertionError("Toggling display should reuse the existing spectra")

    monkeypatch.setattr("omazone.workspace.analyse", unexpected)
    view.show_range.setChecked(False)
    wait()
    assert view.image is None
    assert len(window.spectrum_plot.listDataItems()) == 3
    view.show_range.setChecked(True)
    wait()
    assert view.image is not None
    assert view.note.height() >= view.note.heightForWidth(view.note.width())
    for row in window.stage_measurements.values:
        for label in row:
            assert label.height() >= label.minimumSizeHint().height()
    assert len(window.spectrum_plot.listDataItems()) == 6
    assert window.project.config_key() == config and window.project.input_key() == input_key
    assert window.project.revision == revision and window.preview is preview
    np.testing.assert_array_equal(window.project.calibration.whole.coefficients, coefficients)
    np.testing.assert_array_equal(window.output[0], output)


def test_named_capture_describes_selected_reference_passage_and_exports_stats(
    session, tmp_path, monkeypatch
):
    window, view, path, wait, _ = session
    load_reference(window, path, wait)
    window.views.setStep("reference")
    workbench = window.section_workbench
    workbench.target_name.setText("Middle passage")
    workbench.reference_waveform.set_selection(SampleRegion(16000 * 2, 16000 * 7))
    workbench.capture()
    wait()
    target = next(iter(workbench.targets.values()))
    assert target.variation.frames == 16000 * 5
    assert target.variation.sampled_windows == 5
    assert target.region == SampleRegion(32000, 112000)
    workbench.target_list.setCurrentRow(0)
    window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("spectrum"))
    wait()
    assert "2.00-7.00 s" in view.note.text() and "Middle passage" in view.note.text()
    # The profile contains no raw audio and remains useful without the reference file.
    from omazone.sections import load_targets, save_targets

    saved = tmp_path / "profiles.json"
    save_targets(saved, [target])
    restored = load_targets(saved)[0]
    np.testing.assert_array_equal(restored.variation.counts, target.variation.counts)


def test_old_mean_only_target_and_late_results_never_show_old_envelope(session):
    window, view, path, wait, _ = session
    load_reference(window, path, wait)
    old = window.workspace.cached_spectrum
    assert view.image is not None
    window.loaded("reference", load_audio(path))  # Legacy mean-only target, same filename/audio.
    window.workspace.spectrum_ready(old)
    assert view.image is None
    wait()
    assert "No variation data" in view.note.text()
    assert not view.show_range.isEnabled()
    assert len(window.spectrum_plot.listDataItems()) == 2


def test_restored_view_preference_redraws_and_late_results_after_reset_are_ignored(session):
    window, view, path, wait, _ = session
    load_reference(window, path, wait)
    window.workspace.restore_preferences(
        {"viewer_mode": "spectrum", "show_reference_variation": False}
    )
    wait()
    assert not view.show_range.isChecked() and view.image is None
    assert window.workspace.preferences()["show_reference_variation"] is False
    window.workspace.restore_preferences(
        {"viewer_mode": "spectrum", "show_reference_variation": True}
    )
    wait()
    assert view.image is not None
    old = window.workspace.cached_spectrum
    window.new_project()
    window.workspace.spectrum_ready(old)
    assert window.source is None and view.image is None


@pytest.mark.parametrize("width,height", [(820, 560), (900, 768), (1280, 800)])
def test_density_display_keeps_plots_and_fixed_actions_readable(session, width, height):
    window, view, path, wait, app = session
    load_reference(window, path, wait)
    window.resize(width, height)
    for _ in range(8):
        app.processEvents()
    assert window.width() == width and window.height() == height
    assert window.tool_scroll.horizontalScrollBar().maximum() == 0
    assert window.tool_scroll.isAncestorOf(view)
    assert not window.tool_scroll.isAncestorOf(window.process_button)
    assert window.spectrum_plot.getViewBox().height() >= 99
    assert window.eq_plot.getViewBox().height() >= 79
    assert view.image is not None
