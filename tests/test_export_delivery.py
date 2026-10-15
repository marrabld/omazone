"""The Export step's delivery panel: format, dither, headroom, and confirmation."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtWidgets

from omazone.declipping import RepairResult
from omazone.delivery import CEILING, DeliverySettings, measure
from omazone.gui import Window, load_audio


@pytest.fixture
def export_session(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 48000
    audio = np.random.default_rng(7171).normal(0, 0.2, (rate, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    window.show()
    window.loaded("source", load_audio(source))

    def wait():
        deadline = time.monotonic() + 25
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    yield window, wait, tmp_path, app
    if window.worker is not None:
        window.worker.wait()
    window.project.mark_saved()
    window.close()
    app.processEvents()


def render_with_gain(window, wait, gain_db, limiter=False):
    """Render the saved recipe at a known output gain so peaks are predictable."""
    window.output_view.enabled.setChecked(True)
    window.output_view.gain.setValue(gain_db)
    window.output_view.limiter_enabled.setChecked(limiter)
    window.render_saved_recipe()
    wait()
    assert window.output is not None
    return window.output[0]


def choose(window, fmt, dither=True):
    window.export_view.format.setCurrentIndex(window.export_view.format.findData(fmt))
    window.export_view.dither.setChecked(dither)


def refuse_dialog(monkeypatch):
    """Fail if the attenuation dialog is ever shown."""
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "exec",
        lambda *a: pytest.fail("Export asked about attenuation when it should not have."),
    )


@pytest.fixture
def dialog(monkeypatch):
    """Capture the delivery-level dialog and let each test choose an answer."""
    asked = []

    class Answer:
        accept = True
        asked = []

        def __init__(self, *args, **kwargs):
            # The window uses the multi-argument constructor, so the title and
            # body arrive as arguments rather than through setters.
            if len(args) > 1 and isinstance(args[1], str):
                Answer.asked.append(args[1])
            if len(args) > 2 and isinstance(args[2], str):
                Answer.asked.append(args[2])
            self._buttons = []

        def setWindowTitle(self, title):
            Answer.asked.append(title)

        def setIcon(self, icon):
            pass

        def setText(self, text):
            Answer.asked.append(text)

        def setInformativeText(self, text):
            Answer.asked.append(text)

        def addButton(self, *args, **kwargs):
            button = QtWidgets.QPushButton(str(args[0]))
            self._buttons.append(button)
            return button

        def exec(self):
            return 0

        def clickedButton(self):
            return self._buttons[0] if Answer.accept else None

    for name in ("__init__", "setWindowTitle", "setIcon", "setText",
                 "setInformativeText", "addButton", "exec", "clickedButton"):
        monkeypatch.setattr(
            QtWidgets.QMessageBox, name, getattr(Answer, name), raising=False
        )
    Answer.asked = asked
    return Answer


def test_panel_defaults_to_float_and_explains_each_choice(export_session):
    window, _, _, _ = export_session
    view = window.export_view
    assert view.format.currentData() == "float"
    assert [view.format.itemData(i) for i in range(view.format.count())] == [
        "float", "pcm_24", "pcm_16",
    ]
    assert view.dither.isChecked()
    assert not view.dither.isEnabled(), "Float has no quantisation step to dither"
    assert view.export_button.text() == "Export WAV…"
    assert "Render the chain" in view.delivery_note.text()
    choose(window, "pcm_24")
    assert view.dither.isEnabled()
    assert "24-bit WAV" in view.export_button.toolTip()
    choose(window, "pcm_16")
    assert "16-bit WAV" in view.export_button.toolTip()


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_panel_reports_fitting_peak_and_dither_state(export_session, fmt):
    window, wait, _, _ = export_session
    render_with_gain(window, wait, -12)
    choose(window, fmt)
    note = window.export_view.delivery_note.text()
    assert "fits this format" in note and "with dither" in note
    assert f"{measure(window.output[0], window.export_view.delivery_settings()).peak_dbfs:+.2f}" in note
    window.export_view.dither.setChecked(False)
    assert "without dither" in window.export_view.delivery_note.text()
    choose(window, "float")
    assert "No dither is used" in window.export_view.delivery_note.text()


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_over_range_export_asks_before_attenuating_and_writes_the_quieter_file(
    export_session, tmp_path, monkeypatch, dialog, fmt
):
    window, wait, _, _ = export_session
    chain = render_with_gain(window, wait, 6)
    choose(window, fmt)
    assert measure(chain, DeliverySettings(format=fmt)).clipped
    assert "exceeds this format's ceiling" in window.export_view.delivery_note.text()
    target = tmp_path / f"attenuated-{fmt}.wav"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), "")
    )
    dialog.accept = True
    window.export()
    wait()
    assert "Delivery level" in dialog.asked
    assert any("would clip" in item for item in dialog.asked)
    assert any("Float WAV has no ceiling" in item for item in dialog.asked)
    written, rate = sf.read(target, always_2d=True)
    assert sf.info(target).subtype == fmt.upper()
    assert rate == 48000
    assert np.max(np.abs(written)) <= CEILING[fmt]
    assert not np.array_equal(written.astype(np.float64), chain.astype(np.float32))
    assert "attenuated" in window.status.text()
    assert "dBFS" in window.status.text()


def test_cancelling_the_dialog_writes_nothing(export_session, tmp_path, monkeypatch, dialog):
    window, wait, _, _ = export_session
    render_with_gain(window, wait, 6)
    choose(window, "pcm_24")
    target = tmp_path / "cancelled.wav"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), "")
    )
    dialog.accept = False
    window.export()
    assert not target.exists()
    assert window.worker is None


def test_float_export_never_asks_even_when_over_zero_dbfs(
    export_session, tmp_path, monkeypatch
):
    window, wait, _, _ = export_session
    chain = render_with_gain(window, wait, 18)
    assert np.max(np.abs(chain)) > 1.0
    choose(window, "float")
    target = tmp_path / "float-loud.wav"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), "")
    )
    refuse_dialog(monkeypatch)
    window.export()
    wait()
    written, _ = sf.read(target, always_2d=True)
    assert sf.info(target).subtype == "FLOAT"
    np.testing.assert_allclose(written, chain.astype(np.float32), rtol=1e-7)


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_quiet_integer_export_writes_without_a_prompt(
    export_session, tmp_path, monkeypatch, fmt
):
    window, wait, _, _ = export_session
    render_with_gain(window, wait, -18)
    choose(window, fmt)
    target = tmp_path / f"quiet-{fmt}.wav"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), "")
    )
    refuse_dialog(monkeypatch)
    window.export()
    wait()
    assert sf.info(target).subtype == fmt.upper()
    written, _ = sf.read(target, always_2d=True, dtype="float64")
    assert written.shape == window.output[0].shape
    assert np.max(np.abs(written)) <= CEILING[fmt]


def test_delivery_choices_do_not_touch_the_saved_recipe_or_audition(export_session):
    window, wait, _, _ = export_session
    window.compressor_view.enabled.setChecked(True)
    window.compressor_view.threshold.setValue(-24)
    window.compressor_view.ratio.setValue(3)
    render_with_gain(window, wait, -9)
    window.views.setStep("export")
    output_stage = dict(window.project.stages["output"].parameters)
    dynamics = dict(window.project.stages["dynamics"].parameters)
    limiter = dict(window.project.stages["limiter"].parameters)
    chain = window.output[0].copy()
    preview = window.preview
    audition = window.audition_mode
    choose(window, "pcm_16")
    window.export_view.dither.setChecked(True)
    window.export_view.dither.setChecked(False)
    choose(window, "float")
    assert window.project.stages["output"].parameters == output_stage
    assert window.project.stages["dynamics"].parameters == dynamics
    assert window.project.stages["limiter"].parameters == limiter
    np.testing.assert_array_equal(window.output[0], chain)
    assert window.preview is preview
    assert window.audition_mode == audition


def test_full_chain_is_exported_even_while_auditioning_a_band_solo(
    export_session, tmp_path, monkeypatch
):
    window, wait, _, _ = export_session
    window.compressor_view.mode.setCurrentIndex(1)
    window.compressor_view.enabled.setChecked(True)
    window.compressor_view.threshold.setValue(-30)
    window.compressor_view.ratio.setValue(4)
    chain = render_with_gain(window, wait, -12)
    window.views.setStep("export")
    window.apply_comparison("band-solo")
    assert window.audition_mode == "band-solo"
    solo = window.compressor_view.solo_pair()
    assert solo is not None and not np.array_equal(solo[0], chain)
    choose(window, "float")
    target = tmp_path / "while-soloed.wav"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), "")
    )
    window.export()
    wait()
    written, _ = sf.read(target, always_2d=True)
    np.testing.assert_allclose(written, chain.astype(np.float32), rtol=1e-7)


@pytest.mark.parametrize("fmt", ["float", "pcm_24", "pcm_16"])
def test_silence_exports_at_every_format(export_session, tmp_path, monkeypatch, fmt):
    window, wait, _, _ = export_session
    render_with_gain(window, wait, -60)
    silence = np.zeros_like(window.output[0])
    window.output = (silence, *window.output[1:])
    window.chain_result = type(window.chain_result)(
        **{
            **{
                field: getattr(window.chain_result, field)
                for field in window.chain_result.__dataclass_fields__
                if field != "output"
            },
            "output": silence,
        }
    )
    choose(window, fmt)
    target = tmp_path / f"silence-{fmt}.wav"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), "")
    )
    window.export()
    wait()
    assert sf.info(target).frames == len(silence)
    assert sf.info(target).subtype == "FLOAT" if fmt == "float" else fmt.upper()
    written, _ = sf.read(target, always_2d=True, dtype="float64")
    assert np.max(np.abs(written)) < 1e-3


def test_repaired_source_export_stays_a_64_bit_float_file(
    export_session, tmp_path, monkeypatch
):
    """Repair export is a separate archival path and must not follow the panel."""
    window, wait, _, _ = export_session
    audio = window.source[0]
    window.repair_result = RepairResult(audio, (), ())
    choose(window, "pcm_16")
    target = tmp_path / "repaired.wav"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), "")
    )
    window.export_repair()
    wait()
    assert sf.info(target).subtype == "DOUBLE"
    written, _ = sf.read(target, always_2d=True)
    np.testing.assert_array_equal(written, audio)


def test_delivery_panel_is_reachable_and_resets_between_projects(export_session, tmp_path, monkeypatch):
    window, wait, _, _ = export_session
    window.views.setStep("export")
    assert window.export_view.delivery.isVisibleTo(window.export_view)
    saved = tmp_path / "delivery.omazone.json"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(saved), "")
    )
    window.save_current_project()
    wait()
    window.new_project()
    assert window.export_view.format.currentData() == "float"
    assert window.export_view.dither.isChecked()
    window.open_project_path(saved)
    wait()
    assert window.export_view.format.currentData() == "float", (
        "Delivery format is a per-export choice, not part of the recipe"
    )