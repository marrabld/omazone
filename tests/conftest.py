"""Shared GUI test defaults: an unattended run must never stop for a click."""

import pytest
from PySide6 import QtCore, QtWidgets

_OK = QtWidgets.QMessageBox.StandardButton.Ok
_DISCARD = QtWidgets.QMessageBox.StandardButton.Discard

# What an unattended run should answer, and with what. Discarding lets a test
# carry on past an unsaved project; a cancelled file dialog means "no file".
ANSWERS = {
    ("QMessageBox", "question"): _DISCARD,
    ("QMessageBox", "warning"): _OK,
    ("QMessageBox", "critical"): _OK,
    ("QMessageBox", "information"): _OK,
}


class FakeAudioBackend:
    """Deterministic PortAudio metadata. Never enumerate a test machine's devices."""

    def __init__(self):
        self.devices = [
            {
                "index": 0,
                "name": "Test output",
                "hostapi": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
        ]
        self.apis = [{"name": "Test backend"}]
        self.default_index = 0
        self.checks = []
        self.unsupported_rates = set()
        self.error = None

    def query_hostapis(self):
        if self.error:
            raise RuntimeError(self.error)
        return self.apis

    def query_devices(self, device=None, kind=None):
        if self.error:
            raise RuntimeError(self.error)
        if device is None and kind is None:
            return list(self.devices)
        index = self.default_index if device is None else device
        return next(item for item in self.devices if item["index"] == index).copy()

    def check_output_settings(self, *, device, channels, dtype, samplerate):
        self.checks.append(
            dict(device=device, channels=channels, dtype=dtype, samplerate=samplerate)
        )
        if samplerate in self.unsupported_rates:
            raise RuntimeError("Invalid sample rate")


@pytest.fixture
def audio_backend(monkeypatch):
    api = FakeAudioBackend()
    monkeypatch.setattr("omazone.audio_devices.backend", lambda: api)
    return api


@pytest.fixture(autouse=True)
def isolated_audio_preferences(monkeypatch, tmp_path, audio_backend):
    # Two windows in a test share this settings file, but no test can read or
    # overwrite the user's real application preference or depend on their OS.
    monkeypatch.setattr(
        "omazone.audio_device_view.preference_store",
        lambda: QtCore.QSettings(
            str(tmp_path / "audio-preferences.ini"), QtCore.QSettings.Format.IniFormat
        ),
    )


@pytest.fixture(autouse=True)
def no_blocking_dialogs(monkeypatch, request):
    """Answer every modal dialog so the suite can run without a human.

    The guards that stop unsaved work being lost are modal on purpose, so a test
    that dirties a project and closes the window would otherwise hang waiting for
    Save, Discard, or Cancel. This answers for every dialog entry point rather
    than one, so adding a new message box later cannot silently reintroduce a
    prompt that blocks a run.

    A test that needs to exercise a dialog's own behaviour patches the method
    itself, which takes precedence over this default.
    """
    asked = []

    def answer(target, name, reply):
        def handler(*args, **kwargs):
            asked.append(f"{target}.{name}")
            return reply

        return handler

    for (target, name), reply in ANSWERS.items():
        monkeypatch.setattr(
            getattr(QtWidgets, target),
            name,
            answer(target, name, reply),
            raising=False,
        )

    def cancel_file(*args, **kwargs):
        asked.append(f"QFileDialog.{args[1] if len(args) > 1 else '?'}")
        return "", ""

    for name in ("getSaveFileName", "getOpenFileName"):
        monkeypatch.setattr(QtWidgets.QFileDialog, name, cancel_file)

    request.node.dialogs_asked = asked
    return asked
