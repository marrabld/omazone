"""Shared GUI test defaults."""

import pytest
from PySide6 import QtWidgets


@pytest.fixture(autouse=True)
def discard_unsaved_project_prompts(monkeypatch):
    """Keep unrelated GUI tests from blocking when they close a dirty window."""
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Discard,
    )
