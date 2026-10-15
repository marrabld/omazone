"""Real Qt inspector and render-chain tests, without opening an audio device."""

import threading
import time

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets

from omazone.gui import Window, analyse
from omazone.loudness import integrated_lufs
from omazone.stage_measurements import MeasurementCache
from omazone.tool_panel import ToolPanel
from omazone.waveform import SampleRegion


@pytest.fixture
def session():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    t = np.arange(rate * 2) / rate
    audio = (0.25 * np.sin(2 * np.pi * 1000 * t))[:, None]
    audio[rate:] *= 0.2
    window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
    window.show()
    errors = []
    window.error = errors.append

    def wait():
        meter = window.stage_measurements
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if window.worker is None and meter.job is None and not meter.timer.isActive():
                break
            time.sleep(0.005)
        assert window.worker is None and meter.job is None and not meter.timer.isActive()
        assert not errors

    yield window, window.stage_measurements, wait, app
    if window.worker is not None:
        window.worker.wait()
        app.processEvents()
    window.project.mark_saved()
    window.close()
    app.processEvents()


def render_gain(window, wait, limiter=False):
    window.views.setStep("output")
    window.output_view.gain.setValue(-6)
    if limiter:
        window.output_view.ceiling.setValue(-24)
        window.output_view.limiter_enabled.setChecked(True)
    window.render_saved_recipe()
    wait()
    window.output_view.comparison_selector.setCurrentIndex(0)
    wait()


def texts(meter):
    return [[label.text() for label in row] for row in meter.values]


def test_output_measures_stage_boundary_not_final_file_or_matched_audition(session):
    window, meter, wait, _ = session
    render_gain(window, wait, limiter=True)
    state = window.comparison("output-gain")
    assert meter.request.before is state.before and meter.request.after is state.after
    expected = []
    for audio in (state.before, state.after):
        expected.append(
            (
                f"{20 * np.log10(np.max(np.abs(audio))):+.1f}",
                f"{integrated_lufs(audio, 16000):+.1f}",
            )
        )
    assert texts(meter) == [[expected[0][0], expected[1][0]], [expected[0][1], expected[1][1]]]
    assert texts(meter)[0][1] != f"{20 * np.log10(np.max(np.abs(window.output[0]))):+.1f}"
    assert "Whole recording" in meter.scope.text()
    assert "audition matching excluded" in meter.note.text()
    snapshot = texts(meter)
    revision = window.project.revision
    output = window.output[0].copy()
    window.toggle_ab()
    window.workspace.refresh()
    wait()
    assert texts(meter) == snapshot and window.project.revision == revision
    np.testing.assert_array_equal(window.output[0], output)
    window.output_view.comparison_selector.setCurrentIndex(1)
    wait()
    assert meter.request.before is window.limiter_before
    assert meter.request.after is window.limiter_after
    assert "Final limiter" in meter.scope.text()


def test_selection_scope_short_passages_and_cache_reuse(session, monkeypatch):
    window, meter, wait, _ = session
    render_gain(window, wait)
    whole_key = (id(window.output_before), 16000, 0, 32000)
    whole_reading = meter.cache.entries[whole_key][1]
    window.waveform.set_selection(SampleRegion(16000, 32000))
    wait()
    assert "Selected passage 1.000-2.000 s" in meter.scope.text()
    assert texts(meter)[0][0] == f"{20 * np.log10(0.05):+.1f}"
    assert texts(meter)[1][0] == f"{integrated_lufs(window.output_before[16000:], 16000):+.1f}"
    window.waveform.set_selection(SampleRegion(16000, 16100))
    wait()
    assert texts(meter)[1] == ["--", "--"]
    assert "Under 400 ms" in meter.note.text()
    window.waveform.set_selection(None)
    wait()
    assert meter.cache.entries[whole_key][1] is whole_reading
    # Returning after a hidden page must restore, not clear-and-skip, readings.
    old = texts(meter)
    window.views.setStep("export")
    assert meter.isHidden()
    window.views.setStep("output")
    wait()
    assert texts(meter) == old


@pytest.mark.parametrize(
    "step,key",
    [("match", "mastering"), ("eq", "eq"), ("filters", "filters"), ("dynamics", "dynamics")],
)
def test_each_inspector_follows_its_own_comparison_not_final_output(session, step, key):
    window, meter, wait, _ = session
    render_gain(window, wait, limiter=True)
    window.views.setStep(step)
    wait()
    state = window.comparison(key)
    assert meter.request.comparison_key == key
    assert meter.request.before is state.before and meter.request.after is state.after
    assert texts(meter)[0][0] == f"{20 * np.log10(np.max(np.abs(state.before))):+.1f}"
    assert texts(meter)[0][1] == f"{20 * np.log10(np.max(np.abs(state.after))):+.1f}"
    assert texts(meter)[0][1] != f"{20 * np.log10(np.max(np.abs(window.output[0]))):+.1f}"
    assert "Skipped" in meter.note.text()


def test_edit_clears_after_immediately_and_keeps_valid_before(session):
    window, meter, wait, _ = session
    render_gain(window, wait)
    before = window.output_before
    window.output_view.gain.setValue(-9)
    assert texts(meter)[0][1] == "--" and texts(meter)[1][1] == "--"
    assert meter.request.before is before and meter.request.after is None
    wait()
    assert texts(meter)[0][0] != "--" and texts(meter)[0][1] == "--"
    assert "Render" in meter.note.text()


def test_unrendered_upstream_context_has_no_stage_input_reading(session):
    window, meter, wait, _ = session
    # No EQ render exists. The comparison model deliberately retains source
    # context, but metering must not present it as compressor input after EQ.
    window.project.stages["eq"].bypassed = False
    window.project.stages["eq"].parameters = {"kind": "invalid"}
    window.views.setStep("dynamics")
    window.update_buttons()
    wait()
    assert meter.request.before is None and meter.request.after is None
    assert texts(meter) == [["--", "--"], ["--", "--"]]
    assert "input unavailable" in meter.note.text()


def test_worker_runs_off_ui_and_reuses_lufs_from_render(session, monkeypatch):
    window, meter, wait, app = session
    threads = []
    original = MeasurementCache.measure

    def measure(self, *args, **kwargs):
        threads.append(QtCore.QThread.currentThread())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(MeasurementCache, "measure", measure)
    render_gain(window, wait)
    assert threads and all(thread is not app.thread() for thread in threads)
    # Remove scalar entries to prove the renderer's LUFS can seed fresh work.
    meter.cache.entries.clear()

    def unexpected(*args):
        raise AssertionError("Whole-recording LUFS should come from ChainResult")

    monkeypatch.setattr("omazone.stage_measurements.integrated_lufs", unexpected)
    window.views.setStep("match")
    wait()
    window.views.setStep("output")
    wait()
    assert "unavailable" not in meter.note.text()
    assert all(value != "--" for row in texts(meter) for value in row)


def test_late_worker_result_cannot_restore_stale_output(session, monkeypatch):
    window, meter, wait, app = session
    render_gain(window, wait)
    entered, release = threading.Event(), threading.Event()
    original = MeasurementCache.measure
    blocked = False

    def measure(self, *args, **kwargs):
        nonlocal blocked
        if not blocked:
            blocked = True
            entered.set()
            assert release.wait(10)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(MeasurementCache, "measure", measure)
    try:
        window.waveform.set_selection(SampleRegion(16000, 32000))
        deadline = time.monotonic() + 5
        while not entered.is_set() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert entered.is_set()
        window.output_view.gain.setValue(-9)
        assert meter.request.after is None
        release.set()
        wait()
        assert texts(meter)[0][1] == "--" and texts(meter)[1][1] == "--"
        assert "Render" in meter.note.text()
    finally:
        release.set()


def test_silence_and_failed_measurement_are_not_numeric_lufs(session, monkeypatch):
    window, meter, wait, _ = session
    audio = window.source[0].copy()
    audio[16000:] = 0
    window.loaded("source", (audio, 16000, analyse(audio, 16000), "silent passage"))
    render_gain(window, wait)
    window.waveform.set_selection(SampleRegion(16000, 32000))
    wait()
    assert texts(meter)[0] == ["-inf", "-inf"]
    assert texts(meter)[1] == ["--", "--"]
    assert "Silence" in meter.note.text()

    def failed(*args):
        raise RuntimeError("meter test failure")

    monkeypatch.setattr(MeasurementCache, "measure", failed)
    window.waveform.set_selection(SampleRegion(0, 8000))
    wait()
    assert texts(meter) == [["--", "--"], ["--", "--"]]
    assert "Measurement unavailable: meter test failure" in meter.note.text()


@pytest.mark.parametrize("width,height", [(820, 560), (900, 768), (1280, 800)])
def test_meter_scrolls_with_settings_without_horizontal_clipping(session, width, height):
    window, meter, wait, app = session
    render_gain(window, wait)
    window.resize(width, height)
    for _ in range(8):
        app.processEvents()
    assert window.tool_scroll.isAncestorOf(meter)
    assert not window.tool_scroll.isAncestorOf(window.views.action_bar)
    assert window.tool_scroll.horizontalScrollBar().maximum() == 0
    window.tool_scroll.ensureWidgetVisible(meter)
    for _ in range(5):
        app.processEvents()
    assert meter.width() <= window.tool_scroll.viewport().width()


def test_shared_inspector_height_excludes_larger_hidden_pages():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = ToolPanel()
    try:
        for minimum in (40, 2000):
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            label = QtWidgets.QLabel("Inspector content that can wrap onto more lines.")
            label.setWordWrap(True)
            label.setMinimumHeight(minimum)
            layout.addWidget(label)
            panel.stack.addWidget(page)
        panel.show()
        app.processEvents()
        assert panel.settings_content.heightForWidth(250) < 200
        panel.stack.setCurrentIndex(1)
        app.processEvents()
        assert panel.settings_content.heightForWidth(250) >= 2000
    finally:
        panel.close()
