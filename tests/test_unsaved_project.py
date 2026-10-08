"""Protect dirty project recipes before replacing or closing them."""

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtGui, QtWidgets

import omazone.project_controller as project_controller
from omazone.gui import Window, load_audio
from omazone.project import load_project


def dirty_window(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    source = tmp_path / "mix.wav"
    t = np.arange(8000) / 16000
    sf.write(source, np.sin(2 * np.pi * 440 * t)[:, None] * 0.1, 16000, subtype="DOUBLE")
    window.loaded("source", load_audio(source))
    assert window.project.dirty and window.project.source is not None
    return app, window


def wait_for_job(app, window, errors, timeout=10):
    deadline = time.monotonic() + timeout
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert window.worker is None
    return errors


def force_close(app, window):
    if window.worker is not None:
        window.worker.wait()
        app.processEvents()
    window.project.dirty = False
    window.close_approved = True
    window.close()
    app.processEvents()


def test_dirty_guard_cancel_and_discard(tmp_path, monkeypatch):
    app, window = dirty_window(tmp_path)
    called = []
    try:
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "question",
            lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        assert not window.guard_unsaved(lambda: called.append("cancelled"))
        assert called == [] and window.project.dirty

        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "question",
            lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Discard,
        )
        assert window.guard_unsaved(lambda: called.append("discarded"))
        assert called == ["discarded"]
    finally:
        force_close(app, window)


def test_destructive_entry_points_use_the_guard(tmp_path, monkeypatch):
    app, window = dirty_window(tmp_path)
    guarded = []
    loaded = []
    monkeypatch.setattr(window, "guard_unsaved", lambda action: guarded.append(action))
    monkeypatch.setattr(
        window, "load_audio_path", lambda target, path: loaded.append((target, path))
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *args: (str(tmp_path / "chosen.file"), ""),
    )
    try:
        window.new_project()
        window.open_project_path(tmp_path / "project.omazone.json")
        window.load("source")
        assert len(guarded) == 3
        assert loaded == []

        window.load("reference")
        assert len(guarded) == 3
        assert loaded == [("reference", str(tmp_path / "chosen.file"))]

        event = QtGui.QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert len(guarded) == 4
    finally:
        force_close(app, window)


def test_save_then_new_waits_for_successful_worker_finalization(tmp_path, monkeypatch):
    app, window = dirty_window(tmp_path)
    errors = []
    window.error = errors.append
    destination = tmp_path / "saved.omazone.json"
    old_project = window.project
    old_project.match_mode = "none"
    reset = window.reset_project
    continuation_states = []

    def checked_reset():
        continuation_states.append(window.worker is None)
        reset()

    monkeypatch.setattr(window, "reset_project", checked_reset)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Save,
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(destination), "")
    )
    try:
        window.new_project()
        assert window.project is old_project
        assert window.worker is not None
        wait_for_job(app, window, errors)
        assert not errors
        assert continuation_states == [True]
        assert window.project is not old_project and window.source is None
        assert destination.exists()
        assert load_project(destination).source.sha256 == old_project.source.sha256
    finally:
        force_close(app, window)


@pytest.mark.parametrize("failure", ["cancelled-dialog", "write-error"])
def test_failed_save_does_not_run_deferred_new(tmp_path, monkeypatch, failure):
    app, window = dirty_window(tmp_path)
    errors = []
    window.error = errors.append
    original = window.project
    destination = tmp_path / "not-saved.omazone.json"
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Save,
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        lambda *args: ("", "") if failure == "cancelled-dialog" else (str(destination), ""),
    )
    if failure == "write-error":
        monkeypatch.setattr(
            project_controller,
            "save_project",
            lambda *args: (_ for _ in ()).throw(OSError("disk full")),
        )
    try:
        window.new_project()
        if window.worker is not None:
            wait_for_job(app, window, errors)
        assert window.project is original and window.project.dirty
        assert window.source is not None
        if failure == "cancelled-dialog":
            assert errors == []
        else:
            assert errors == ["disk full"]
    finally:
        force_close(app, window)


def test_close_after_save_and_close_during_other_job(tmp_path, monkeypatch):
    app, window = dirty_window(tmp_path)
    window.show()
    destination = tmp_path / "closed.omazone.json"
    errors = []
    window.error = errors.append
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Save,
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(destination), "")
    )
    try:
        assert not window.close()
        wait_for_job(app, window, errors)
        deadline = time.monotonic() + 2
        while window.isVisible() and time.monotonic() < deadline:
            app.processEvents()
        assert destination.exists() and not window.isVisible()

        other = Window()
        other.show()
        other.project.dirty = False
        other.start_job(lambda: time.sleep(0.1), lambda _: None, "Working…")
        assert not other.close()
        wait_for_job(app, other, [])
        assert other.isVisible()
        force_close(app, other)
    finally:
        force_close(app, window)


def test_changed_project_does_not_run_saved_snapshot_continuation(tmp_path, monkeypatch):
    app, window = dirty_window(tmp_path)
    destination = tmp_path / "snapshot.omazone.json"
    started = threading.Event()
    release = threading.Event()
    continued = []
    real_save = project_controller.save_project

    def delayed_save(path, project):
        started.set()
        assert release.wait(5)
        return real_save(path, project)

    monkeypatch.setattr(project_controller, "save_project", delayed_save)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(destination), "")
    )
    try:
        assert window.save_current_project(completed=lambda: continued.append(True))
        assert started.wait(5)
        window.project.touch()
        release.set()
        wait_for_job(app, window, [])
        assert continued == []
        assert window.project.dirty
        assert "newer changes remain unsaved" in window.status.text().lower()
    finally:
        release.set()
        force_close(app, window)


def test_job_callback_failure_restores_controls_and_rejects_overlap(tmp_path):
    app, window = dirty_window(tmp_path)
    errors = []
    window.error = errors.append
    started = threading.Event()
    release = threading.Event()

    def blocked_job():
        started.set()
        assert release.wait(5)
        return "result"

    try:
        window.start_job(
            blocked_job,
            lambda _: (_ for _ in ()).throw(ValueError("callback failed")),
            "Working…",
        )
        assert started.wait(5)
        with pytest.raises(RuntimeError, match="already in progress"):
            window.start_job(lambda: None, lambda _: None, "Second job…")
        release.set()
        wait_for_job(app, window, errors)
        assert errors == ["callback failed"]
        assert window.load_mix.isEnabled()
    finally:
        release.set()
        force_close(app, window)
