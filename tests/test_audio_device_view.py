"""Playback routing and app preference isolation using fake PortAudio streams."""

import copy
import json
import time

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtCore, QtWidgets

from omazone.audio_device_view import PREFERENCE
from omazone.audio_devices import DeviceKey, encode_choice
from omazone.gui import Window, load_audio
from omazone.playback import AuditionProcessor


@pytest.fixture
def device_session(tmp_path, monkeypatch, audio_backend):
    import sounddevice as sd

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    events, opened = [], []

    class Stream:
        latency = 0.032
        close_error = False
        stop_error = False
        start_error = False
        active = False

        def __init__(self, **kwargs):
            self.options = kwargs
            self.callback = kwargs["callback"]
            events.append(("open", kwargs["device"]))
            opened.append(self)

        def start(self):
            events.append(("start", self.options["device"]))
            if self.start_error:
                raise RuntimeError("Device disappeared during start")
            self.active = True

        def stop(self):
            events.append(("stop", self.options["device"]))
            if self.stop_error:
                raise RuntimeError("Stop failed")
            self.active = False

        def close(self):
            events.append(("close", self.options["device"]))
            if self.close_error:
                raise RuntimeError("Close failed")
            self.active = False

    monkeypatch.setattr(sd, "OutputStream", Stream)
    monkeypatch.setattr("omazone.gui.AuditionProcessor", lambda: AuditionProcessor(transition_ms=0))
    audio_backend.devices.append(
        dict(
            index=1, name="Interface", hostapi=0, max_output_channels=2, default_samplerate=48000.0
        )
    )
    window = Window()
    errors = []
    window.error = errors.append
    source = tmp_path / "private-recording.wav"
    sf.write(
        source, np.random.default_rng(866).normal(0, 0.05, (16000, 2)), 16000, subtype="DOUBLE"
    )
    window.loaded("source", load_audio(source))
    window.show()
    view = window.audio_device_view

    def wait():
        deadline = time.monotonic() + 20
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None

    yield window, view, opened, events, errors, wait, app
    for stream in opened:
        stream.close_error = stream.stop_error = False
    window.project.mark_saved()
    window.close()
    app.processEvents()


def choose(view, name="Interface"):
    view.refresh_devices()
    index = next(
        i
        for i in range(view.selector.count())
        if view.selector.itemData(i) == DeviceKey("Test backend", name)
    )
    view.selector.setCurrentIndex(index)


def test_construction_and_source_loading_do_not_enumerate_devices(
    device_session, audio_backend, monkeypatch
):
    window, view, opened, _, _, _, _ = device_session
    assert view.devices is None and not audio_backend.checks and not opened
    assert view.selector.currentText() == "System default"
    window.views.setStep("output")
    assert view.isVisibleTo(window.output_view)
    choose(view)
    assert view.choice == DeviceKey("Test backend", "Interface")
    assert not opened and not audio_backend.checks


def test_switch_stops_and_closes_before_opening_new_device_without_changing_audio(device_session):
    window, view, opened, events, errors, _, _ = device_session
    window.play()
    block = np.empty((128, 2), dtype=np.float32)
    opened[0].callback(block, len(block), None, None)
    position = window.position
    preview = window.preview
    stages, revision = copy.deepcopy(window.project.stages), window.project.revision
    choose(view)
    assert window.stream is None and not window.playing
    assert window.position == position and window.preview is preview
    assert window.project.stages == stages and window.project.revision == revision
    window.play()
    assert opened[-1].options["device"] == 1
    assert events == [
        ("open", 0),
        ("start", 0),
        ("stop", 0),
        ("close", 0),
        ("open", 1),
        ("start", 1),
    ]
    assert opened[-1].options["samplerate"] == 16000
    assert opened[-1].options["dtype"] == "float32"
    assert not errors


def test_preference_survives_restart_and_is_not_saved_in_project(
    device_session, tmp_path, monkeypatch
):
    window, view, _, _, _, wait, app = device_session
    choose(view)
    raw = view.preferences.value(PREFERENCE)
    assert json.loads(raw)["device"] == {"hostapi": "Test backend", "name": "Interface"}
    assert "index" not in raw
    second = Window()
    try:
        assert second.audio_device_view.choice == view.choice
        assert second.audio_device_view.devices is None
    finally:
        second.close()
        app.processEvents()
    saved = tmp_path / "project.omazone.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(saved), ""))
    window.save_current_project()
    wait()
    assert "Interface" not in saved.read_text() and PREFERENCE not in saved.read_text()
    window.new_project()
    assert view.choice == DeviceKey("Test backend", "Interface")


def test_device_removal_preserves_selection_and_does_not_open_default(
    device_session, audio_backend
):
    window, view, opened, _, errors, _, _ = device_session
    choose(view)
    audio_backend.devices.pop()
    window.play()
    assert not opened and not window.playing
    assert "unavailable" in errors[-1] and "No fallback" in errors[-1]
    view.refresh_devices()
    assert "Unavailable" in view.selector.currentText()
    assert view.choice == DeviceKey("Test backend", "Interface")


def test_changed_default_and_changed_session_indices_are_used_fresh(device_session, audio_backend):
    window, view, opened, _, errors, _, _ = device_session
    window.play()
    window.stop()
    audio_backend.default_index = 1
    window.play()
    assert opened[-1].options["device"] == 1
    window.stop()
    choose(view)
    audio_backend.devices[-1]["index"] = 7
    window.play()
    assert opened[-1].options["device"] == 7
    assert not errors


def test_unsupported_rate_is_checked_before_opening_and_does_not_retarget(
    device_session, audio_backend
):
    window, view, opened, _, errors, _, _ = device_session
    choose(view)
    audio_backend.unsupported_rates.add(16000)
    window.play()
    assert not opened
    assert "16000 Hz" in errors[-1] and "does not resample" in errors[-1]
    assert view.choice == DeviceKey("Test backend", "Interface")
    assert all(item["device"] == 1 for item in audio_backend.checks)


def test_refresh_and_failed_enumeration_do_not_switch_running_stream(device_session, audio_backend):
    window, view, opened, events, _, _, _ = device_session
    window.play()
    before = list(events)
    view.refresh_devices()
    assert events == before and window.stream is opened[0]
    audio_backend.error = "Backend offline"
    view.refresh_devices()
    assert "Backend offline" in view.note.text()
    assert window.stream is opened[0]


def test_start_and_stream_state_failures_close_safely_without_fallback(device_session):
    window, view, opened, events, errors, _, _ = device_session
    choose(view)
    # Force a failure after construction, so cleanup has a real fake stream to close.
    import sounddevice as sd

    stream_type = sd.OutputStream
    stream_type.start_error = True
    window.play()
    assert window.stream is None and not window.playing
    assert events[-2:] == [("stop", 1), ("close", 1)]
    assert "disappeared" in errors[-1]
    stream_type.start_error = False
    window.play()
    opened[-1].active = False
    window.check_playback()
    assert window.stream is None and not window.playing
    assert "disconnected" in view.note.text()
    window.stop()
    assert "disconnected" in view.note.text(), "Cleanup must not erase the cause of failure"


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [44100, 48000])
def test_exact_mono_stereo_format_reaches_stream(device_session, tmp_path, channels, rate):
    window, view, opened, _, errors, _, _ = device_session
    source = tmp_path / "routing.wav"
    audio = np.random.default_rng(86).normal(0, 0.05, (rate // 10, channels))
    sf.write(source, audio, rate, subtype="DOUBLE")
    window.loaded("source", load_audio(source))
    choose(view)
    window.play()
    assert opened[-1].options["channels"] == channels
    assert opened[-1].options["samplerate"] == rate
    output = np.empty((128, channels), dtype=np.float32)
    opened[-1].callback(output, len(output), None, None)
    np.testing.assert_array_equal(output, audio[:128].astype(np.float32))
    assert not errors


def test_stop_error_still_closes_and_close_error_blocks_new_stream(device_session):
    window, view, opened, events, _, _, _ = device_session
    window.play()
    opened[-1].stop_error = True
    assert window.stop()
    assert window.stream is None and events[-1] == ("close", 0)
    window.play()
    opened[-1].close_error = True
    count = len(opened)
    choose(view)
    assert view.choice is None, "A switch must not proceed while the old stream is unclosed"
    window.play()
    assert len(opened) == count
    assert "Could not close" in view.note.text()


def test_late_callback_after_stop_cannot_play_or_advance_new_audio(device_session):
    import sounddevice as sd

    window, _, opened, _, _, _, _ = device_session
    window.play()
    stale = opened[0]
    assert window.stop()
    window.play()
    position = window.position
    buffer = np.ones((128, 2), dtype=np.float32)
    with pytest.raises(sd.CallbackStop):
        stale.callback(buffer, len(buffer), None, None)
    np.testing.assert_array_equal(buffer, 0)
    assert window.position == position and window.playing


def test_unknown_preference_is_retained_until_explicit_choice(device_session):
    window, view, _, _, _, _, app = device_session
    raw = '{"version": 99, "future": "keep this"}'
    view.preferences.setValue(PREFERENCE, raw)
    view.preferences.sync()
    other = Window()
    try:
        panel = other.audio_device_view
        assert panel.preference_error and "unsupported" in panel.selector.currentText()
        assert panel.preferences.value(PREFERENCE) == raw
        panel.selector.setCurrentIndex(0)
        assert panel.preference_error is None
        assert panel.preferences.value(PREFERENCE) == encode_choice(None)
    finally:
        other.close()
        app.processEvents()


def test_unwritable_preferences_keep_session_choice_with_warning(device_session):
    window, view, opened, _, errors, _, _ = device_session

    class Unwritable:
        def setValue(self, *args):
            raise OSError("read-only settings")

    view.preferences = Unwritable()
    choose(view)
    assert view.choice == DeviceKey("Test backend", "Interface")
    assert "session only" in view.note.text()
    window.play()
    assert opened[-1].options["device"] == 1 and not errors


def test_ambiguous_and_insufficient_outputs_are_disabled(device_session, audio_backend):
    _, view, _, _, _, _, _ = device_session
    audio_backend.devices.extend(
        [
            dict(
                index=2,
                name="Interface",
                hostapi=0,
                max_output_channels=2,
                default_samplerate=48000.0,
            ),
            dict(
                index=3,
                name="Mono output",
                hostapi=0,
                max_output_channels=1,
                default_samplerate=48000.0,
            ),
        ]
    )
    view.refresh_devices()
    assert view.selector.model().item(0).isEnabled()
    for index in range(1, view.selector.count()):
        key = view.selector.itemData(index)
        if key.name in {"Interface", "Mono output"}:
            assert not view.selector.model().item(index).isEnabled()


def test_diagnostics_include_actual_device_and_underflows_without_private_paths(device_session):
    window, view, opened, _, _, _, app = device_session
    choose(view)
    window.play()

    class Underflow:
        output_underflow = True

    opened[0].callback(np.empty((128, 2), dtype=np.float32), 128, None, Underflow())
    view.copy_button.click()
    text = app.clipboard().text()
    assert "Interface [Test backend]" in text and "16000 Hz, 2 channel(s), float32" in text
    assert "32.0 ms" in text and "Output underflows: 1" in text
    assert "private-recording.wav" not in text
    assert "OS:" in text and "Audible behaviour still requires" in text
    window.stop()
    assert "Stream: Stopped" in view.diagnostics()


@pytest.mark.parametrize("width,height", [(820, 660), (1024, 768), (1400, 1000)])
def test_device_selector_stays_reachable_in_narrow_output_panel(device_session, width, height):
    window, view, _, _, _, _, app = device_session
    window.resize(width, height)
    window.views.setStep("output")
    view.refresh_devices()
    for _ in range(5):
        app.processEvents()
    assert window.tool_scroll.horizontalScrollBar().maximum() == 0
    assert view.width() <= window.tool_scroll.viewport().width()
    view.selector.setFocus()
    assert view.selector.focusPolicy() != QtCore.Qt.FocusPolicy.NoFocus
