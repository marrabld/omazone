import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pyqtgraph as pg
import soundfile as sf
from PySide6 import QtWidgets

from omazone.engine import analyse
from omazone.gui import Window, load_audio
from omazone.project import NamedRegion
from omazone.sections import capture_target
from omazone.waveform import SampleRegion


def wait_jobs(app, window):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        app.processEvents()
        if (
            window.worker is None
            and window.workspace.job is None
            and not window.workspace.timer.isActive()
        ):
            return
        time.sleep(0.01)
    raise AssertionError("Workspace jobs did not finish")


def test_context_survives_every_tool_and_reference_selection():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(91).normal(0, 0.1, (32000, 2))
    try:
        window.resize(1100, 950)
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
        window.loaded("reference", (audio[:16000], rate, analyse(audio[:16000], rate), "reference"))
        selection = SampleRegion(8000, 20000)
        window.waveform.set_selection(selection)
        window.waveform.channel_plots[0].setXRange(0.4, 1.3, padding=0)
        window.position = 9000
        window.loop_selection.setChecked(True)
        window.project.regions.append(NamedRegion("guitar", "Guitar", selection))
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        for index in range(window.views.count()):
            window.views.setCurrentIndex(index)
            app.processEvents()
            assert window.waveform.isVisible()
            assert window.workspace.overview.isVisible()
            assert window.waveform.selection == selection
            np.testing.assert_allclose(window.waveform.channel_plots[0].viewRange()[0], [0.4, 1.3])
            assert window.position == 9000
            assert window.transport.region == selection and window.transport.loop
            assert window.workspace.mode.currentData() == "both"
        window.views.setCurrentIndex(2)
        window.section_workbench.reference_waveform.select_seconds(0.1, 0.4)
        assert window.waveform.selection == selection
        assert window.position == 9000
        assert window.workspace.reference_pane.isVisible()
        assert len(window.workspace.overview_regions) == 2
        assert any(
            isinstance(item, pg.TextItem) and item.toPlainText() == "Guitar"
            for item in window.workspace.overview_regions
        )
        wait_jobs(app, window)
    finally:
        window.close()


def test_actual_output_visualisation_and_pending_input_context():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(92).normal(0, 0.1, (16000, 1))
    reference = audio * 0.5
    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
        window.loaded("reference", (reference, rate, analyse(reference, rate), "reference"))
        window.views.setCurrentIndex(0)
        window.waveform.set_selection(SampleRegion(1000, 10000))
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("output"))
        assert "not been rendered" in window.workspace.badge.text()
        assert window.waveform.index.audio is audio
        window.process()
        wait_jobs(app, window)
        assert window.waveform.index.audio is window.output[0]
        assert "output" in window.workspace.label.text()
        assert window.listen_processed
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("spectrum"))
        wait_jobs(app, window)
        assert window.workspace.overview.isVisible()
        assert window.workspace.spectra.isVisible()
        assert not window.waveform.isVisible()
        assert len(window.spectrum_plot.listDataItems()) == 3
        window.amount.setValue(60)
        assert window.output is None
        assert "not been rendered" in window.workspace.badge.text()
        assert window.waveform.index.audio is audio
        assert window.workspace.signal.currentData() == "output"  # Requested preference retained.
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("original"))
        assert window.audition_mode == "original"
        assert window.preview is None
        assert window.waveform.index.audio is audio
        window.resize(820, 800)
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        app.processEvents()
        assert window.waveform.isVisible() and window.workspace.spectra.isVisible()
        assert window.tool_scroll.widgetResizable()
    finally:
        window.close()


def test_profile_only_reference_keeps_mix_visible_and_project_preferences(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(93).normal(0, 0.1, (16000, 1))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    project = tmp_path / "workspace.omazone.json"
    try:
        window.show()
        window.loaded("source", load_audio(source))
        target = capture_target(
            audio, rate, SampleRegion(0, 16000), "Saved clean target", "clean", "reference.wav"
        )
        window.section_workbench.target_captured(target)
        window.views.setCurrentIndex(2)
        window.section_workbench.target_list.setCurrentRow(0)
        app.processEvents()
        assert window.workspace.reference_pane.isVisible()
        assert window.workspace.target_plot.isVisible()
        assert not window.section_workbench.reference_waveform.isVisible()
        assert "Saved clean target" in window.workspace.reference_label.text()
        assert window.waveform.isVisible()
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        window.waveform.set_selection(SampleRegion(2000, 10000))
        window.waveform.channel_plots[0].setXRange(0.1, 0.8, padding=0)
        wait_jobs(app, window)
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(project), "")
        )
        window.save_current_project()
        wait_jobs(app, window)
        window.new_project()
        window.open_project_path(project)
        wait_jobs(app, window)
        assert not errors
        assert window.workspace.mode.currentData() == "both"
        assert window.waveform.selection == SampleRegion(2000, 10000)
        np.testing.assert_allclose(window.waveform.channel_plots[0].viewRange()[0], [0.1, 0.8])
        assert len(window.project.targets) == 1
    finally:
        window.close()
