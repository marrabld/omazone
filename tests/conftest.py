"""Shared GUI test defaults: an unattended run must never stop for a click."""

import pytest
from PySide6 import QtWidgets

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
