"""Exercise worker handoff, plot updates, and float-WAV export without a display."""

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtCore, QtTest, QtWidgets
from scipy import signal

from omazone.comparison import SPECS, Side
from omazone.engine import analyse, audition_pair
from omazone.gui import Window, load_audio
from omazone.sections import SectionAssignment
from omazone.waveform import PeakIndex, SampleRegion
from omazone.workflow_status import AnalysisState, RenderState
from omazone.workflow_steps import KEYS, LEGACY_ORDER, step_for


def test_load_render_export(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    audio = np.random.default_rng(3).normal(0, 0.1, (16000, 2))
    mix = tmp_path / "mix.wav"
    reference = tmp_path / "reference.wav"
    exported = tmp_path / "export.wav"
    sf.write(mix, audio, 48000, subtype="FLOAT")
    sf.write(reference, signal.lfilter([1], [1, -0.8], audio, axis=0), 48000)

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None, "Worker timed out"
        assert not errors

    try:
        window.show()
        window.loaded("source", load_audio(mix))
        window.loaded("reference", load_audio(reference))
        window.process()
        wait()
        assert window.output[0].shape == audio.shape
        assert len(window.spectrum_plot.listDataItems()) == 3
        assert len(window.eq_plot.listDataItems()) == 2
        window.toggle_ab()
        assert window.listen_processed
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(exported), "")
        )
        window.export()
        wait()
        result, rate = sf.read(exported, always_2d=True)
        assert rate == 48000
        assert sf.info(exported).subtype == "FLOAT"
        np.testing.assert_allclose(result, window.output[0], atol=1e-7)
        window.amount.setValue(75)
        assert window.output is None
        assert not window.export_button.isEnabled()
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_scrub_and_ab_share_cursor_without_audio_device(monkeypatch):
    import sounddevice as sd

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.active = False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 48000
    audio = np.random.default_rng(4).normal(0, 0.1, (rate * 3, 2)).astype(np.float32)
    try:
        assert not window.seek_slider.isEnabled()
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix.wav"))
        window.preview = (audio, audio * 0.5)
        window.output = (audio * 0.5, analyse(audio, rate), None)
        window.update_buttons()
        window.show()
        app.processEvents()
        window.seek_slider.setValue(rate)
        assert window.position == rate
        assert window.time_label.text() == "0:01.0 / 0:03.0"
        window.play()
        block = np.empty((128, 2), dtype=np.float32)
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, audio[rate : rate + 128])
        window.toggle_ab()
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, audio[rate + 128 : rate + 256] * 0.5)

        # Real mouse events exercise click-to-seek, stop-before-seek, and resume.
        old_stream = window.stream
        slider = window.seek_slider
        point = QtCore.QPoint(slider.width() * 3 // 4, slider.height() // 2)
        QtTest.QTest.mousePress(slider, QtCore.Qt.MouseButton.LeftButton, pos=point)
        assert not window.playing
        assert not old_stream.active
        QtTest.QTest.mouseRelease(slider, QtCore.Qt.MouseButton.LeftButton, pos=point)
        assert window.playing
        assert window.listen_processed
        start = window.position
        assert abs(start - len(audio) * 0.75) < rate * 0.1
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, audio[start : start + 128] * 0.5)

        window.check_playback()
        assert window.seek_slider.value() == window.position
        window.play()  # Pause retains cursor.
        assert not window.playing
        assert window.position == start + 128
        window.seek_slider.setValue(len(audio))
        window.play()
        assert window.position == 0
        window.stop()
        window.loaded("source", (audio[:rate], rate, analyse(audio[:rate], rate), "short.wav"))
        assert window.position == 0
        assert window.seek_slider.maximum() == rate
    finally:
        window.close()


def test_waveform_selection_zoom_seek_and_new_file_reset():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 48000
    audio = np.random.default_rng(5).normal(0, 0.1, (rate * 2, 2))
    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "stereo.wav", PeakIndex(audio)))
        app.processEvents()
        view = window.waveform
        assert len(view.channel_plots) == 2
        view.select_seconds(0.25, 1.5)
        assert view.selection == SampleRegion(12000, 72000)
        view.regions[1].setRegion((0.5, 1.0))
        assert view.selection == SampleRegion(24000, 48000)
        assert view.regions[0].getRegion() == (0.5, 1.0)
        view.zoom_selection()
        app.processEvents()
        np.testing.assert_allclose(view.channel_plots[0].viewRange()[0], [0.5, 1.0])
        np.testing.assert_allclose(view.channel_plots[1].viewRange()[0], [0.5, 1.0])
        view.seek_start()
        assert window.position == 24000
        assert view.playheads[0].value() == 0.5
        view.select_seconds(-1, 10)
        assert view.selection == SampleRegion(0, len(audio))
        view.select_seconds(1 / rate, 2 / rate)
        assert view.selection == SampleRegion(1, 2)
        assert "1 samples" in view.selection_label.text()
        view.set_selection(None)
        assert not any(item.isVisible() for item in view.regions)
        view.fit_song()
        view.set_selection(None)
        app.processEvents()
        plot = view.channel_plots[0]
        viewport = plot.viewport()
        start_point = plot.mapFromScene(plot.getViewBox().mapViewToScene(QtCore.QPointF(0.2, 0)))
        end_point = plot.mapFromScene(plot.getViewBox().mapViewToScene(QtCore.QPointF(0.8, 0)))
        QtTest.QTest.mousePress(
            viewport,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
            start_point,
        )
        QtTest.QTest.mouseMove(viewport, end_point, 20)
        QtTest.QTest.mouseRelease(
            viewport,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
            end_point,
        )
        app.processEvents()
        assert view.selection is not None
        assert abs(view.selection.start / rate - 0.2) < 0.02
        assert abs(view.selection.end / rate - 0.8) < 0.02

        view.set_selection(None)
        QtTest.QTest.mouseClick(viewport, QtCore.Qt.MouseButton.LeftButton, pos=start_point)
        assert abs(window.position / rate - 0.2) < 0.02
        view.select_view()
        assert view.selection == SampleRegion(0, len(audio))

        # At sample-level zoom, inspect the actual source points rather than an envelope.
        view.channel_plots[0].setXRange(100 / rate, 110 / rate, padding=0)
        view.redraw()
        times, values = view.curves[0].getData()
        samples = np.rint(times * rate).astype(int)
        np.testing.assert_array_equal(values, audio[samples, 0])

        mono = audio[:rate, :1]
        window.loaded("source", (mono, rate, analyse(mono, rate), "mono.wav", PeakIndex(mono)))
        app.processEvents()
        assert view.selection is None
        assert len(view.channel_plots) == 1
        assert not view.regions[0].isVisible()
        assert view.end_time.maximum() == 1
    finally:
        window.close()


def test_selection_playback_looping_edits_and_mode_changes(monkeypatch):
    import sounddevice as sd

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 48000
    audio = np.random.default_rng(8).normal(0, 0.1, (128, 2)).astype(np.float32)
    try:
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix.wav"))
        assert not window.play_selection_button.isEnabled()
        window.waveform.set_selection(SampleRegion(3, 8))
        assert window.play_selection_button.isEnabled()
        window.preview = (audio, audio * 0.5)
        window.play_selection_button.click()
        block = np.empty((3, 2), dtype=np.float32)
        window.stream.callback(block, 3, None, None)
        np.testing.assert_array_equal(block, audio[3:6])
        with pytest.raises(sd.CallbackStop):
            window.stream.callback(block, 3, None, None)
        np.testing.assert_array_equal(block[:2], audio[6:8])
        assert not np.any(block[2])
        assert window.position == 8
        window.check_playback()
        assert not window.playing

        window.loop_selection.setChecked(True)
        window.play_selection_button.click()
        block = np.empty((13, 2), dtype=np.float32)
        window.stream.callback(block, 13, None, None)
        np.testing.assert_array_equal(block, audio[3 + np.arange(13) % 5])
        assert window.position == 6
        window.toggle_ab()
        window.stream.callback(block, 13, None, None)
        np.testing.assert_array_equal(block, audio[3 + (3 + np.arange(13)) % 5] * 0.5)
        assert window.position == 4
        window.play()  # Pause.
        assert not window.playing
        window.play()
        assert window.position == 4

        window.waveform.set_selection(SampleRegion(20, 25))
        assert not window.playing
        assert window.resume_after_selection_edit
        window.finish_selection_edit()
        assert window.playing
        assert window.position == 20
        assert window.transport.loop
        window.seek(22)
        assert window.transport.loop
        window.seek(40)
        assert window.playing
        assert window.transport.region is None
        assert not window.loop_selection.isChecked()
        assert window.position == 40

        window.loop_selection.setChecked(True)
        window.waveform.set_selection(SampleRegion(30, 35))
        window.stop()  # Cancels pending resume after editing.
        QtTest.QTest.qWait(180)
        app.processEvents()
        assert not window.playing
        window.play_selection()
        window.waveform.set_selection(None)
        window.finish_selection_edit()
        assert window.playing
        assert window.transport.region is None
        assert not window.loop_selection.isChecked()

        window.waveform.set_selection(SampleRegion(120, 128))
        window.loop_selection.setChecked(True)
        window.play_selection()
        window.loop_selection.setChecked(False)
        assert window.playing
        assert not window.transport.loop
        window.whole_song_button.click()
        assert window.transport.region is None
        window.loop_selection.setChecked(True)
        window.loaded("source", (audio[:64], rate, analyse(audio[:64], rate), "short.wav"))
        assert window.transport.region is None
        assert not window.loop_selection.isChecked()
        assert not window.playing
    finally:
        window.close()


def test_reference_targets_two_sections_render_edit_and_export(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    mix = np.random.default_rng(21).normal(0, 0.1, (rate * 2, 2))
    reference = mix.copy()
    reference[rate:] = signal.sosfilt(
        signal.butter(2, 1200, fs=rate, output="sos"), mix[rate:], axis=0
    )

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None
        assert not errors

    try:
        window.show()
        window.loaded("source", (mix, rate, analyse(mix, rate), "mix"))
        window.waveform.set_selection(SampleRegion(0, rate))
        window.loaded("reference", (reference, rate, analyse(reference, rate), "reference"))
        workbench = window.section_workbench
        workbench.reference_waveform.set_selection(SampleRegion(0, rate))
        workbench.target_name.setText("Metal")
        workbench.capture_button.click()
        wait()
        workbench.reference_waveform.set_selection(SampleRegion(rate, len(reference)))
        workbench.target_name.setText("Clean")
        workbench.capture_button.click()
        wait()
        assert len(workbench.targets) == 2
        assert window.waveform.selection == SampleRegion(0, rate)

        workbench.use_mix_selection()
        workbench.name.setText("Metal intro")
        workbench.target_choice.setCurrentIndex(0)
        workbench.add_section()
        assert len(workbench.sections) == 1
        window.waveform.set_selection(SampleRegion(rate, len(mix)))
        workbench.use_mix_selection()
        workbench.name.setText("Clean outro")
        workbench.target_choice.setCurrentIndex(1)
        workbench.amount.setValue(100)
        workbench.add_section()
        assert len(workbench.sections) == 2
        assert len(window.waveform.section_items) > 0
        workbench.render_button.click()
        wait()
        assert len(window.section_result.curves) == 2
        assert window.output[0].shape == mix.shape
        assert window.export_button.isEnabled()

        window.views.setCurrentWidget(workbench)
        workbench.table.selectRow(0)
        assert window.views.currentWidget() is workbench
        assert window.waveform.selection == SampleRegion(0, rate)
        assert "Metal intro" in window.eq_plot.plotItem.titleLabel.text
        export = tmp_path / "sections.wav"
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(export), "")
        )
        window.export()
        wait()
        result, saved_rate = sf.read(export, always_2d=True)
        assert saved_rate == rate
        np.testing.assert_allclose(result, window.output[0], atol=1e-7)

        workbench.amount.setValue(25)
        assert window.output is None
        assert workbench.draft_dirty
        workbench.update_section()
        assert window.output is None
        assert window.section_result is None
        assert not window.export_button.isEnabled()
        assert workbench.sections[0].settings.amount == 0.25

        workbench.amount.setValue(30)
        workbench.render_all()  # Rendering applies valid pending edits.
        wait()
        assert workbench.sections[0].settings.amount == 0.3
        assert window.section_result is not None

        workbench.end.setValue(1.5)  # Overlaps the second section.
        workbench.update_section()
        assert errors and "overlap" in errors.pop()
        assert workbench.sections[0].region.end == rate

        profiles = tmp_path / "profiles.json"
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(profiles), "")
        )
        workbench.save_library()
        wait()
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getOpenFileName", lambda *args: (str(profiles), "")
        )
        workbench.load_library()
        wait()
        assert len(workbench.targets) == 4
        assert len(workbench.sections) == 2
        window.loaded("source", (mix[:rate], rate, analyse(mix[:rate], rate), "short"))
        assert workbench.sections == []
        assert len(workbench.targets) == 4
        assert not window.waveform.section_items
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_clipping_inspection_markers_navigation_and_stale_results():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 48000
    audio = np.zeros((rate, 2))
    audio[100:110, 0] = 0.6
    audio[150:165, 0] = -0.4
    audio[200:210, 1] = 1.2

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None
        assert not errors

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "test"))
        window.waveform.set_selection(SampleRegion(50, 300))
        inspector = window.clipping_inspector
        inspector.positive.setValue(0.6)
        inspector.negative.setValue(-0.4)
        output = audio * 0.5
        window.output = (output, analyse(output, rate), None)
        window.update_buttons()
        inspector.analyse_button.click()
        wait()
        assert len(inspector.report.candidates) == 2
        assert len(inspector.report.overloads) == 1
        assert window.output[0] is output  # Inspection never modifies render/export audio.
        assert len(window.waveform.clipping_markers) == 2
        inspector.table.selectRow(0)
        inspector.show_interval()
        app.processEvents()
        window.waveform.redraw()
        assert window.position == 100
        assert window.waveform.selection == SampleRegion(50, 300)
        assert window.views.currentWidget() is window.region_page
        assert window.waveform.isVisible()
        assert any(len(marker.points()) > 0 for marker, _, _ in window.waveform.clipping_markers)
        inspector.tolerance.setValue(0.0001)
        assert inspector.report is None
        assert not window.waveform.clipping_items
        inspector.analyse()
        wait()
        window.waveform.set_selection(SampleRegion(50, 250))
        assert inspector.report is None
        inspector.channel.setCurrentIndex(1)  # Left only.
        inspector.suggest()
        wait()
        assert inspector.positive.value() == 0.6
        assert inspector.negative.value() == -0.4
        window.loaded("source", (audio[:, :1], rate, analyse(audio[:, :1], rate), "mono"))
        assert inspector.channel.currentData() == 0
        assert inspector.positive.value() == 1
        assert inspector.report is None
        assert not inspector.analyse_button.isEnabled()
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_selective_repair_audition_export_matching_and_reset(tmp_path, monkeypatch):
    import sounddevice as sd

    from omazone.engine import design_match
    from omazone.sections import capture_target

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 48000
    t = np.arange(rate // 4) / rate
    clean = (0.9 * np.sin(2 * np.pi * 440 * t))[:, None]
    clipped = np.clip(clean, -0.45, 0.6)
    before = clipped.copy()

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None
        assert not errors

    try:
        window.show()
        window.loaded("source", (clipped, rate, analyse(clipped, rate), "clipped"))
        window.waveform.set_selection(SampleRegion(0, len(clipped)))
        inspector = window.clipping_inspector
        inspector.positive.setValue(0.6)
        inspector.negative.setValue(-0.45)
        inspector.analyse()
        wait()
        assert not inspector.repair_button.isEnabled()
        inspector.check_shown(True)
        inspector.repair_button.click()
        wait()
        assert window.repair_result and len(window.repair_result.repaired) > 100
        assert window.audition_mode == "repair"
        assert window.preview_mode.currentData() == "repair"
        assert window.processing_source()[0] is window.repair_result.audio
        assert window.source[0] is clipped
        np.testing.assert_array_equal(clipped, before)
        assert window.waveform.repair_items
        window.loop_selection.setChecked(True)
        window.play_selection()
        block = np.empty((128, 1), dtype=np.float32)
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, window.repair_preview[0][:128])
        window.toggle_ab()
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, window.repair_preview[1][128:256])
        assert window.ab_button.text() == "Listening: repaired"
        window.stop()

        export = tmp_path / "repaired.wav"
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(export), "")
        )
        inspector.export_repair_button.click()
        wait()
        saved, saved_rate = sf.read(export, always_2d=True)
        assert saved_rate == rate
        assert sf.info(export).subtype == "DOUBLE"
        np.testing.assert_array_equal(saved, window.repair_result.audio)

        repaired = window.repair_result
        window.loaded("reference", (clean, rate, analyse(clean, rate), "clean"))
        window.views.setCurrentWidget(window.match_page)
        window.process()
        wait()
        expected = design_match(analyse(repaired.audio, rate), analyse(clean, rate), rate)
        np.testing.assert_allclose(window.output[2].coefficients, expected.coefficients)
        assert window.audition_mode == "mastering"
        assert window.ab_button.text() == "Listening: repaired input"
        assert window.workspace.signal.currentData() == "input"
        workbench = window.section_workbench
        workbench.target_captured(
            capture_target(clean, rate, SampleRegion(0, len(clean)), "Clean", "clean", "generated")
        )
        workbench.use_mix_selection()
        workbench.add_section()
        workbench.render_all()
        wait()
        np.testing.assert_array_equal(
            window.section_result.curves[0].source.power, analyse(repaired.audio, rate).power
        )
        previous_output = window.output
        inspector.max_run_ms.setValue(0.01)  # All checked runs exceed this limit.
        inspector.repair()
        wait()
        assert window.repair_result is repaired
        assert window.output is previous_output
        assert "kept unchanged" in inspector.repair_summary.text()

        window.preview_mode.setCurrentIndex(1)
        assert window.preview is window.repair_preview
        inspector.reset_repair_button.click()
        assert window.repair_result is None
        assert window.processing_source() is window.source
        assert window.output is None
        assert not window.waveform.repair_items
        assert window.audition_mode == "mastering"
        np.testing.assert_array_equal(window.source[0], before)
        assert all(
            inspector.table.item(row, 7).text() == ""
            and inspector.table.item(row, 7).toolTip() == ""
            for row in range(len(inspector.rows))
        )

        inspector.max_run_ms.setValue(1)
        inspector.repair()
        wait()
        assert window.repair_result is not None
        window.loaded("source", (clean, rate, analyse(clean, rate), "new file"))
        assert window.repair_result is None
        assert not window.waveform.repair_items
        assert not inspector.export_repair_button.isEnabled()
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_guided_clipping_flow_hides_details_and_handles_stereo_automatically(monkeypatch):
    import sounddevice as sd

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 48000
    t = np.arange(4800) / rate
    audio = np.column_stack(
        (
            np.clip(0.9 * np.sin(2 * np.pi * 440 * t), -0.45, 0.6),
            np.clip(0.7 * np.sin(2 * np.pi * 660 * t), -0.25, 0.4),
        )
    )

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None
        assert not errors

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "stereo"))
        window.waveform.set_selection(SampleRegion(0, len(audio)))
        inspector = window.clipping_inspector
        # Even with a prior master, entering repair inspection auditions raw source.
        window.output = (audio * 0.5, analyse(audio * 0.5, rate), None)
        window.views.setCurrentWidget(inspector)
        app.processEvents()
        assert window.audition_mode == "original"
        assert window.mastering_controls.isHidden()
        assert window.load_ref.isHidden()
        assert window.preview_mode.isHidden()
        assert inspector.advanced_panel.isHidden()
        assert inspector.review_panel.isHidden()
        window.play_selection()
        block = np.empty((32, 2), dtype=np.float32)
        window.stream.callback(block, 32, None, None)
        np.testing.assert_array_equal(block, audio[:32].astype(np.float32))
        window.stop()
        inspector.analyse_button.click()
        wait()
        assert {item.channel for item in inspector.report.candidates} == {0, 1}
        assert "dBFS" not in inspector.summary.text()
        assert not inspector.repair_button.isEnabled()
        inspector.review_button.click()
        assert not inspector.review_panel.isHidden()
        assert inspector.table.isColumnHidden(6)
        inspector.check_shown(True)
        inspector.repair_button.click()
        wait()
        assert window.views.currentWidget() is inspector
        assert inspector.result_heading.text() == "Repair preview ready"
        assert not inspector.result_actions.isHidden()
        inspector.listen_button.click()
        # Choosing a comparison is a listening choice, not a transport choice.
        assert window.playing and not window.transport.loop
        window.stream.callback(block, 32, None, None)
        np.testing.assert_array_equal(block, window.repair_preview[0][:32])
        window.toggle_ab()
        window.stream.callback(block, 32, None, None)
        np.testing.assert_array_equal(block, window.repair_preview[1][32:64])
        window.stop()
        inspector.advanced_toggle.setChecked(True)
        assert not inspector.advanced_panel.isHidden()
        assert not inspector.table.isColumnHidden(6)
        inspector.channel.setCurrentIndex(1)
        inspector.positive.setValue(0.6)
        inspector.negative.setValue(-0.45)
        inspector.manual_analyse_button.click()
        wait()
        assert len(inspector.report.stats) == 1
        assert inspector.report.stats[0].channel == 0
        window.views.setStep("match")
        assert not window.mastering_controls.isHidden()
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_guided_no_results_message_does_not_offer_repair():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 48000
    audio = (0.8 * np.sin(2 * np.pi * 440 * np.arange(4800) / rate))[:, None]
    try:
        window.loaded("source", (audio, rate, analyse(audio, rate), "clean"))
        window.waveform.set_selection(SampleRegion(0, len(audio)))
        from omazone.clipping import find_clipping

        inspector = window.clipping_inspector
        inspector.analysed(find_clipping(audio, window.waveform.selection))
        assert inspector.result_heading.text() == "No clear clipped peaks found"
        assert "isolated recording" in inspector.summary.text()
        assert not inspector.review_button.isEnabled()
        assert not inspector.repair_button.isEnabled()
    finally:
        app.processEvents()
        window.close()


def test_saved_project_restores_recipe_and_renders_without_relearning(tmp_path, monkeypatch):
    from omazone.gui import load_audio
    from omazone.sections import capture_target

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 48000
    t = np.arange(12000) / rate
    clean = (0.9 * np.sin(2 * np.pi * 440 * t))[:, None]
    clipped = np.clip(clean, -0.45, 0.6)
    source_path, reference_path = tmp_path / "source.wav", tmp_path / "reference.wav"
    sf.write(source_path, clipped, rate, subtype="DOUBLE")
    sf.write(reference_path, clean, rate, subtype="DOUBLE")
    project_path = tmp_path / "session.omazone.json"

    def wait():
        deadline = time.monotonic() + 20
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None
        assert not errors

    try:
        window.loaded("source", load_audio(source_path))
        window.waveform.set_selection(SampleRegion(0, len(clipped)))
        inspector = window.clipping_inspector
        inspector.find_peaks()
        wait()
        inspector.check_shown(True)
        inspector.repair()
        wait()
        assert window.project.repairs
        window.loaded("reference", load_audio(reference_path))
        workbench = window.section_workbench
        workbench.target_captured(
            capture_target(clean, rate, SampleRegion(0, len(clean)), "Clean", "clean", "generated")
        )
        workbench.use_mix_selection()
        workbench.name.setText("Verse")
        workbench.add_section()
        workbench.render_all()
        wait()
        expected = window.output[0].copy()
        section_id = workbench.sections[0].id
        coefficients = window.project.calibration.sections[0].filter.coefficients.copy()
        window.project.stages["eq"].parameters = {"band": {"region_id": section_id, "gain_db": -2}}
        window.waveform.set_selection(SampleRegion(2000, 8000))
        window.waveform.channel_plots[0].setXRange(0.03, 0.2, padding=0)
        window.position = 4000
        window.loop_selection.setChecked(True)
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(project_path), "")
        )
        window.save_current_project()
        wait()
        assert not window.project.dirty
        window.new_project()
        assert window.source is None
        window.open_project_path(project_path)
        wait()
        assert workbench.sections[0].id == section_id
        assert window.project.stages["eq"].parameters["band"]["gain_db"] == -2
        assert window.repair_result is not None
        assert window.waveform.selection == SampleRegion(2000, 8000)
        assert window.position == 4000 and window.transport.loop
        np.testing.assert_allclose(window.waveform.channel_plots[0].viewRange()[0], [0.03, 0.2])
        assert window.output is None and window.project.can_render_saved_match
        assert window.workflow_status().matching_analysis is AnalysisState.CURRENT
        assert not window.workflow_status().export_available
        window.render_saved_recipe()
        wait()
        np.testing.assert_allclose(window.output[0], expected, atol=1e-12)
        np.testing.assert_array_equal(
            window.project.calibration.sections[0].filter.coefficients, coefficients
        )
        window.set_stage_bypass("repair", True)
        assert window.processing_source() is window.source
        assert window.project.needs_reanalysis and window.project.can_render_saved_match
        assert window.workflow_status().matching_analysis is AnalysisState.RETAINED
        assert window.project.repairs and workbench.sections
        window.render_saved_recipe()
        wait()
        np.testing.assert_array_equal(
            window.project.calibration.sections[0].filter.coefficients, coefficients
        )
        assert window.project.needs_reanalysis  # Saved rendering did not silently relearn.
        assert window.workflow_status().matching_analysis is AnalysisState.RETAINED
        assert window.workflow_status().export_available

        # Missing files retain the recipe and opaque future-stage settings.
        source_path.rename(tmp_path / "moved.wav")
        window.open_project_path(project_path)
        wait()
        assert window.source is None
        assert window.project.sections[0].id == section_id
        assert workbench.sections[0].id == section_id
        assert window.project.stages["eq"].parameters
        assert any("missing" in item for item in window.asset_messages)
        assert not window.render_saved_action.isEnabled()
        window.project.relink("source", tmp_path / "moved.wav")
        from omazone.project import hydrate_project

        window.install_project(hydrate_project(window.project), project_path)
        assert window.source is not None
        assert window.project.sections[0].id == section_id
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_project_open_preserves_hidden_matching_precision_and_rejects_invalid_view(tmp_path):
    from omazone.engine import MatchSettings
    from omazone.project import AudioReference, Project, save_project

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    audio = np.random.default_rng(77).normal(0, 0.1, (16000, 1))
    path = tmp_path / "source.wav"
    sf.write(path, audio, 16000, subtype="DOUBLE")
    project = Project(
        source=AudioReference.from_path(path),
        matching=MatchSettings(
            amount=0.555555, smoothing_octaves=0, max_boost_db=20.123, taps=4097
        ),
    )
    file = tmp_path / "precise.omazone.json"
    save_project(file, project)

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None

    try:
        window.open_project_path(file)
        wait()
        assert not errors
        window.sync_project()
        assert window.project.matching == project.matching
        original = window.project
        import json

        data = json.loads(file.read_text())
        data["view"]["active_step"] = "not a step"
        file.write_text(json.dumps(data))
        window.open_project_path(file)
        wait()
        assert errors and "step" in errors.pop()
        assert window.project is original

        # A stale position is ignored once the project names its step.
        data["view"]["active_step"] = "output"
        data["view"]["active_tool"] = len(KEYS) + 5
        file.write_text(json.dumps(data))
        window.open_project_path(file)
        wait()
        assert not errors
        assert window.views.currentStep() == "output"
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_rendering_after_viewing_original_restores_a_usable_comparison(tmp_path, monkeypatch):
    """Regression: rendering a stage while viewing the original must not strand A/B.

    The inspector said "EQ rendered" while the listening button stayed disabled,
    because the viewer was still showing the untouched original recording.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(228).normal(0, 0.1, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    try:
        window.show()
        window.loaded("source", load_audio(source))
        eq = window.manual_eq_view
        eq.add_band(2200, -4)
        window.views.setCurrentWidget(eq)
        # The user browses the untouched recording before rendering the stage.
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("original"))
        app.processEvents()
        assert window.audition_mode == "original"
        assert not window.ab_button.isEnabled()

        eq.render_button.click()
        wait()
        assert window.eq_preview is not None
        assert window.audition_mode == "eq"
        assert window.ab_button.isEnabled()
        assert window.ab_button.text() == "Listening: before EQ"
        window.toggle_ab()
        assert window.ab_button.text() == "Listening: after EQ"
        assert window.preview is window.eq_preview

        # Viewing the original stays available as an explicit choice, and returning
        # to the step comparison must work again.
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("original"))
        app.processEvents()
        assert window.audition_mode == "original" and not window.ab_button.isEnabled()
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("input"))
        app.processEvents()
        assert window.audition_mode == "eq" and window.ab_button.isEnabled()
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_rendering_compression_after_viewing_original_restores_comparison(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(229).normal(0, 0.2, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    try:
        window.show()
        window.loaded("source", load_audio(source))
        comp = window.compressor_view
        comp.enabled.setChecked(True)
        window.views.setCurrentWidget(comp)
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("original"))
        app.processEvents()
        assert not window.ab_button.isEnabled()

        comp.render_button.click()
        wait()
        assert window.dynamics_preview is not None
        assert window.audition_mode == "dynamics"
        assert window.ab_button.isEnabled()
        assert window.ab_button.text() == "Listening: before compression"
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_rendering_output_after_viewing_original_restores_comparison(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(231).normal(0, 0.2, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    try:
        window.show()
        window.loaded("source", load_audio(source))
        output = window.output_view
        output.gain.setValue(-3)
        window.views.setCurrentWidget(output)
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("original"))
        app.processEvents()
        assert not window.ab_button.isEnabled()

        output.render_button.click()
        wait()
        assert window.output_preview is not None
        assert window.audition_mode == "output-gain"
        assert window.ab_button.isEnabled()
        assert window.ab_button.text() == "Listening: before output gain"
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_stage_viewer_and_playback_use_the_same_chain_prefixes(tmp_path, monkeypatch):
    import sounddevice as sd

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(232).normal(0, 0.2, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    try:
        window.show()
        window.loaded("source", load_audio(source))
        window.manual_eq_view.add_band(2200, -4)
        window.compressor_view.enabled.setChecked(True)
        window.output_view.gain.setValue(-3)
        window.views.setCurrentWidget(window.output_view)
        window.output_view.render_button.click()
        wait()
        result = window.chain_result
        assert result is not None

        cases = (
            (
                window.manual_eq_view,
                result.matched,
                result.equalized,
                window.eq_preview,
                "Manual EQ",
            ),
            (
                window.compressor_view,
                result.equalized,
                result.pre_output,
                window.dynamics_preview,
                "Compression",
            ),
            (
                window.output_view,
                result.pre_output,
                result.output,
                window.output_preview,
                "Output gain",
            ),
        )
        for view, expected_before, expected_after, preview, expected_title in cases:
            window.views.setCurrentWidget(view)
            before, after, actual_rate, title = window.workspace.pairs()
            assert actual_rate == rate
            assert title == expected_title
            np.testing.assert_array_equal(before, expected_before)
            np.testing.assert_array_equal(after, expected_after)
            expected_preview = audition_pair(expected_before, expected_after)
            np.testing.assert_array_equal(preview[0], expected_preview[0])
            np.testing.assert_array_equal(preview[1], expected_preview[1])

        bypass_cases = (
            (window.manual_eq_view.enabled, window.manual_eq_view, "Manual EQ (bypassed)"),
            (
                window.compressor_view.enabled,
                window.compressor_view,
                "Compression (bypassed)",
            ),
            (window.output_view.enabled, window.output_view, "Output gain (bypassed)"),
        )
        for enabled, view, expected_title in bypass_cases:
            enabled.setChecked(False)
            window.views.setCurrentWidget(window.output_view)
            window.output_view.render_button.click()
            wait()
            window.views.setCurrentWidget(view)
            before, after, _, title = window.workspace.pairs()
            assert title == expected_title
            np.testing.assert_array_equal(after, before)
            enabled.setChecked(True)

        # Editing a stage clears the completed chain but intentionally retains
        # that stage's valid input. The viewer and Play must keep using it.
        window.views.setCurrentWidget(window.output_view)
        retained = window.output_before
        window.output_view.gain.setValue(-4)
        before, after, _, _ = window.workspace.pairs()
        assert after is None
        np.testing.assert_array_equal(before, retained)

        window.views.setCurrentWidget(window.manual_eq_view)
        before, after, _, _ = window.workspace.pairs()
        np.testing.assert_array_equal(before, result.matched)
        np.testing.assert_array_equal(after, result.equalized)
        window.views.setCurrentWidget(window.compressor_view)
        before, after, _, _ = window.workspace.pairs()
        np.testing.assert_array_equal(before, result.equalized)
        np.testing.assert_array_equal(after, result.pre_output)

        window.views.setCurrentWidget(window.output_view)
        window.play()
        expected = audition_pair(retained, retained)
        np.testing.assert_array_equal(window.preview[0], expected[0])
        np.testing.assert_array_equal(window.preview[1], expected[1])
        window.stop()
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_comparison_model_covers_every_stage_and_preserves_cursor_and_loop(tmp_path, monkeypatch):
    """The one model must serve every pair, the viewer, and the transport."""
    import sounddevice as sd

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(241).normal(0, 0.2, (32000, 2))
    reference = np.random.default_rng(242).normal(0, 0.2, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    try:
        window.show()
        window.loaded("source", load_audio(source))
        window.loaded("reference", (reference, rate, analyse(reference, rate), "reference"))
        window.process()
        wait()
        window.manual_eq_view.add_band(2200, -4)
        window.compressor_view.enabled.setChecked(True)
        window.output_view.gain.setValue(-3)
        window.views.setCurrentWidget(window.output_view)
        window.output_view.render_button.click()
        wait()
        result = window.chain_result
        assert result is not None

        # Every stage comparison names the same pair the viewer and transport use.
        cases = (
            ("mastering", window.match_page, result.repaired, result.matched),
            ("eq", window.manual_eq_view, result.matched, result.equalized),
            ("dynamics", window.compressor_view, result.equalized, result.pre_output),
            ("output-gain", window.output_view, result.pre_output, result.output),
        )
        for key, view, expected_before, expected_after in cases:
            window.views.setCurrentWidget(view)
            before, after, actual_rate, _ = window.workspace.pairs()
            state = window.comparison(key, Side.BEFORE)
            assert actual_rate == rate == state.rate
            np.testing.assert_array_equal(before, expected_before)
            np.testing.assert_array_equal(after, expected_after)
            np.testing.assert_array_equal(state.before, expected_before)
            np.testing.assert_array_equal(state.after, expected_after)
            assert state.available
            assert state.aligned
            expected = audition_pair(expected_before, expected_after)
            np.testing.assert_array_equal(state.playback_arrays[0], expected[0])
            np.testing.assert_array_equal(state.playback_arrays[1], expected[1])

        # Bypassing a stage and rendering it again makes both sides identical.
        window.views.setCurrentWidget(window.output_view)
        for stage, key in (("eq", "eq"), ("dynamics", "dynamics"), ("output", "output-gain")):
            window.set_stage_bypass(stage, True)
            window.output_view.render_button.click()
            wait()
            state = window.comparison(key, Side.BEFORE)
            assert state.available
            assert state.bypassed
            # The viewer names the skipped step, and both sides are the same signal.
            assert window.workspace.viewer_title(key).endswith("(bypassed)")
            np.testing.assert_array_equal(state.before, state.after)
            window.set_stage_bypass(stage, False)
        window.output_view.render_button.click()
        wait()

        # Cursor, selection, and loop belong to the transport, not to the comparison.
        window.views.setCurrentWidget(window.output_view)
        window.waveform.set_selection(SampleRegion(4000, 12000))
        window.loop_selection.setChecked(True)
        window.position = 6000
        window.apply_comparison("eq", Side.BEFORE)
        assert window.position == 6000
        assert window.transport.loop
        assert window.transport.region == SampleRegion(4000, 12000)
        window.apply_comparison("eq", Side.AFTER)
        assert window.position == 6000
        assert window.transport.loop
        window.toggle_ab()
        assert window.position == 6000
        assert window.transport.loop
        assert window.ab_button.text() == "Listening: before EQ"

        # Choosing a step starts on Before; original-only is a separate mode.
        window.apply_comparison("dynamics", Side.AFTER)
        assert window.listen_processed
        window.apply_comparison("output-gain")
        assert not window.listen_processed
        window.apply_comparison("original")
        assert window.audition_mode == "original"
        assert not window.ab_button.isEnabled()

        # A stale stage explains itself beside the control, not only on hover.
        window.views.setCurrentWidget(window.output_view)
        window.output_view.gain.setValue(-4)
        window.apply_comparison("output-gain", Side.BEFORE)
        assert not window.comparison("output-gain").available
        assert window.ab_reason.text() == window.workflow_status().stages["output"].reason
        assert window.ab_reason.text()
        assert not window.ab_button.isEnabled()
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_a_running_stream_always_has_a_comparable_pair(tmp_path, monkeypatch):
    """Changing step mid-playback must not leave the callback without audio."""
    import sounddevice as sd

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(251).normal(0, 0.1, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    block = np.empty((128, 2), dtype=np.float32)

    def drain():
        window.stream.callback(block, 128, None, None)
        assert np.abs(block).max() > 0

    try:
        window.show()
        window.loaded("source", load_audio(source))
        # Play before anything is rendered, then visit every step comparison.
        window.play()
        assert window.playing
        for view in (
            window.manual_eq_view,
            window.compressor_view,
            window.output_view,
            window.match_page,
            window.manual_eq_view,
        ):
            window.views.setCurrentWidget(view)
            app.processEvents()
            assert window.playing, view
            assert window.preview is not None, view
            np.testing.assert_array_equal(window.preview[0], window.preview[1])
            drain()
            window.toggle_ab()
            assert window.preview is not None
            drain()
        window.waveform.set_selection(SampleRegion(4000, 20000))
        window.loop_selection.setChecked(True)
        window.views.setCurrentWidget(window.output_view)
        app.processEvents()
        assert window.transport.loop
        drain()
        assert not errors
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_the_hidden_selector_and_the_selected_comparison_never_disagree(tmp_path):
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(252).normal(0, 0.1, (16000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    try:
        window.show()
        window.loaded("source", load_audio(source))
        # Each comparison row in the selector must be the comparison it names.
        for position, spec in enumerate(SPECS):
            assert window.preview_mode.itemData(position) == spec.key
            assert window.preview_mode.itemText(position) == (
                f"{spec.before_label} / {spec.after_label}"
            )
            window.apply_comparison(spec.key, Side.BEFORE)
            assert window.audition_mode == spec.key
            assert window.preview_mode.currentData() == spec.key
            assert window.preview_mode.currentIndex() == position
            assert window.comparison().key == spec.key
    finally:
        window.close()


def test_choosing_step_output_after_the_original_is_honoured(tmp_path):
    """The visible selector is the user's preference, not a leftover side."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(253).normal(0, 0.1, (16000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def render():
        window.views.setCurrentWidget(window.output_view)
        window.output_view.gain.setValue(-3)
        window.output_view.render_button.click()
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None

    try:
        window.show()
        window.loaded("source", load_audio(source))
        window.manual_eq_view.add_band(2200, -4)
        render()
        assert window.comparison("output-gain").available

        selector = window.workspace.signal
        selector.setCurrentIndex(selector.findData("original"))
        assert window.audition_mode == "original"
        assert not window.listen_processed

        # Asking for the rendered output after listening to the original must work.
        selector.setCurrentIndex(selector.findData("output"))
        assert window.audition_mode == "output-gain"
        assert window.listen_processed
        assert selector.currentData() == "output"
        assert window.ab_button.text() == "Listening: after output gain"

        selector.setCurrentIndex(selector.findData("input"))
        assert window.audition_mode == "output-gain"
        assert not window.listen_processed
        assert window.ab_button.text() == "Listening: before output gain"
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_saved_session_reopens_on_the_same_named_step(tmp_path, monkeypatch):
    """Navigation order is an implementation detail, so sessions store step names."""
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "session.omazone.json"
    rate = 16000
    audio = np.random.default_rng(261).normal(0, 0.1, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    window = Window()
    errors = []
    window.error = errors.append

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            QtWidgets.QApplication.instance().processEvents()
            time.sleep(0.005)
        assert window.worker is None

    try:
        window.loaded("source", load_audio(source))
        window.project.source = window.source[5]
        window.views.setStep("listen")
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        window.waveform.set_selection(SampleRegion(4000, 20000))
        window.loop_selection.setChecked(True)
        monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
        window.save_current_project()
        wait()
        assert not errors

        saved = json.loads(path.read_text())["view"]
        assert saved["active_step"] == "listen"
        assert saved["viewer_tool_modes"]["listen"] == "both"
        assert "active_tool" not in saved

        # A session written before steps were named still opens on the same page.
        legacy = json.loads(path.read_text())
        view = dict(legacy["view"])
        view.pop("active_step")
        view["active_tool"] = LEGACY_ORDER.index("listen")
        view["viewer_tool_modes"] = {
            str(LEGACY_ORDER.index("listen")): view["viewer_tool_modes"].pop("listen")
        }
        legacy["view"] = view
        path.write_text(json.dumps(legacy))

        reopened = Window()
        reopened.error = errors.append
        reopened.open_project_path(path)
        deadline = time.monotonic() + 15
        while reopened.worker is not None and time.monotonic() < deadline:
            QtWidgets.QApplication.instance().processEvents()
            time.sleep(0.005)
        assert reopened.worker is None
        assert not errors
        assert reopened.views.currentStep() == "listen"
        assert reopened.workspace.mode.currentData() == "both"
        assert reopened.transport.loop
        reopened.close()
    finally:
        window.close()


def test_continue_only_moves_and_requires_the_step_to_be_ready_or_skipped(tmp_path):
    """Navigation must never render, bake audio, or relearn a target."""
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(262).normal(0, 0.1, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    try:
        window.loaded("source", load_audio(source))
        assert window.views.currentStep() == "listen"
        assert not window.back_button.isEnabled()

        # Listen and Mark is never skipped and always lets the learner move on.
        assert not window.skip_button.isVisible()
        window.go_continue()
        assert window.views.currentStep() == "repair"
        # Navigating must not render, bake audio, or relearn a target.
        assert window.worker is None
        assert window.output is None
        assert window.chain_result is None
        assert window.project.calibration is None
        assert not errors

        window.go_back()
        assert window.views.currentStep() == "listen"

        # Repair is optional, so it can be skipped, and skipping keeps going.
        window.views.setStep("repair")
        assert window.skip_button.text() == "Skip Repair"
        window.skip_button.click()
        assert window.project.stages["repair"].bypassed
        assert window.views.currentStep() == "match"
        # Skipping retains any choices already made.
        window.project.stages["repair"].parameters = {"note": "kept"}
        window.set_stage_bypass("repair", False)
        assert window.project.stages["repair"].parameters == {"note": "kept"}

        window.views.setStep("repair")
        assert window.skip_button.text() == "Skip Repair", "un-skipping offers to skip again"
        window.set_stage_bypass("repair", True)
        assert window.skip_button.text() == "Enable Repair"
    finally:
        window.close()


def test_continue_names_the_action_when_a_step_is_not_ready(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(263).normal(0, 0.1, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    try:
        window.loaded("source", load_audio(source))
        window.manual_eq_view.enabled.setChecked(True)
        window.manual_eq_view.add_band(2200, -4)
        window.views.setStep("eq")
        assert not window.continue_button.isEnabled()
        expected = window.workflow_status().stages["eq"].reason
        window.go_continue()
        assert window.views.currentStep() == "eq", "Continue must not leave an unfinished step"
        # The reason belongs beside the control, not in a status line that the
        # narrow layout hides.
        assert window.views.action_label.text() == expected
        assert expected in window.continue_button.toolTip()
        assert not errors

        window.views.setStep("output")
        window.output_view.enabled.setChecked(True)
        window.output_view.gain.setValue(-3)
        window.output_view.render_button.click()
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None
        assert window.views.currentStep() == "output"
        window.go_continue()
        assert window.views.currentStep() == "export"
        assert not window.continue_button.isEnabled(), "Export is the final step"
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_navigation_shows_each_step_state_and_export_reviews_the_chain(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(264).normal(0, 0.1, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def titles():
        return {
            key: window.views.navigation.tabText(window.views.indexOfStep(key)).split(" — ")[0]
            for key in window.views.visibleSteps()
        }

    def states():
        return {
            key: window.views.navigation.tabText(window.views.indexOfStep(key))
            for key in window.views.visibleSteps()
        }

    try:
        window.loaded("source", load_audio(source))
        assert titles() == {
            "listen": "Listen and Mark",
            "repair": "Repair",
            "match": "Match",
            "eq": "Manual EQ",
            "dynamics": "Dynamics",
            "output": "Output",
            "export": "Export",
        }
        assert "skipped" in states()["repair"]

        window.manual_eq_view.enabled.setChecked(True)
        window.manual_eq_view.add_band(2200, -4)
        assert "action needed" in states()["eq"]

        window.views.setStep("output")
        window.output_view.enabled.setChecked(True)
        window.output_view.gain.setValue(-3)
        window.output_view.render_button.click()
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None
        assert "ready" in states()["output"]
        assert "ready" in states()["export"]

        window.views.setStep("export")
        rows = [
            window.export_view.chain.item(i).text() for i in range(window.export_view.chain.count())
        ]
        assert rows == [
            "Repair: skipped",
            "Match: skipped",
            "Manual EQ: ready",
            "Dynamics: skipped",
            "Output gain: ready",
        ]
        assert window.export_view.export_button.isEnabled()
        assert window.export_view.peaks.text()
        assert not errors
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_secondary_pages_belong_to_match_and_navigate_with_it(tmp_path):
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(265).normal(0, 0.1, (16000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    try:
        window.show()
        window.loaded("source", load_audio(source))
        # Reference targets and Mix sections are pages of Match, not bar entries.
        for secondary in ("reference", "sections"):
            window.views.setStep(secondary)
            assert not window.views.navigation.isTabVisible(window.views.indexOfStep(secondary))
            assert window.back_button.isEnabled()
            window.go_back()
            assert window.views.currentStep() == "repair"
            window.views.setStep("match")
        assert window.views.navigation.isTabVisible(window.views.indexOfStep("match"))

        # The Match step must actually reach its own pages, or target capture
        # and section matching become unreachable.
        window.views.setStep("match")
        window.match_subnav_reference.click()
        assert window.views.currentStep() == "reference"
        assert window.workspace.reference_pane.isVisible()
        window.match_subnav_sections.click()
        assert window.views.currentStep() == "sections"
        window.match_subnav_match.click()
        assert window.views.currentStep() == "match"
        assert window.match_subnav_match.isChecked()
        assert not window.match_subnav_sections.isChecked()
    finally:
        window.close()


def test_narrow_window_keeps_back_and_continue_and_no_horizontal_scrolling(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(266).normal(0, 0.1, (16000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    try:
        window.show()
        window.loaded("source", load_audio(source))
        for width, height in ((700, 700), (900, 760)):
            window.resize(width, height)
            app.processEvents()
            window.views.setStep("eq")
            app.processEvents()
            assert window.tool_scroll.horizontalScrollBar().maximum() == 0
            window.views.setStep("repair")
            app.processEvents()
            for control in (window.back_button, window.continue_button, window.skip_button):
                assert control.isVisible(), control
                on_window = control.mapTo(window, QtCore.QPoint(0, 0))
                assert window.rect().contains(QtCore.QRect(on_window, control.size())), control
            window.views.setStep("export")
            app.processEvents()
            assert window.tool_scroll.horizontalScrollBar().maximum() == 0
            assert window.minimumSizeHint().width() <= 520, window.minimumSizeHint().width()
    finally:
        window.close()


def test_acceptance_journey_one_without_a_reference(tmp_path, monkeypatch):
    """Load a mix, mark a passage, skip optional stages, set gain, export.

    This is acceptance journey 1 from docs/workflow-consolidation.md: every
    disabled primary action explains itself next to the control.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(267).normal(0, 0.1, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    export = tmp_path / "final.wav"

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None

    def disabled_reason(control):
        """A disabled action must say what to do, not just look broken."""
        assert not control.isEnabled()
        # The reason belongs beside the control, not only in a hover tooltip.
        assert window.views.action_label.text().strip()
        return window.views.action_label.text()

    try:
        window.show()
        window.loaded("source", load_audio(source))
        assert window.views.currentStep() == "listen"

        # Mark a passage without any reference loaded.
        window.waveform.set_selection(SampleRegion(4000, 20000))
        window.region_name.setText("Verse")
        window.name_region_button.click()
        assert [region.name for region in window.project.regions] == ["Verse"]
        assert window.waveform.selection == SampleRegion(4000, 20000)

        # Repair is optional: skip it and move on.
        window.views.setStep("repair")
        window.skip_button.click()
        assert window.project.stages["repair"].bypassed
        assert window.views.currentStep() == "match"

        # Matching needs a reference, and says so rather than failing silently.
        reason = disabled_reason(window.process_button)
        assert "reference" in reason.lower()
        window.views.setStep("match")
        window.skip_button.click()
        assert window.project.stages["match"].bypassed
        assert window.views.currentStep() == "eq"

        # Manual EQ and Dynamics are optional, and start out skipped until the
        # learner turns them on, so Continue passes straight through them.
        for step in ("eq", "dynamics"):
            window.views.setStep(step)
            assert window.project.stages[step].bypassed
            assert window.skip_button.text() == f"Enable {step_for(step).title}"
            window.go_continue()
        assert window.views.currentStep() == "output"

        # Output review stays in the workflow even with no processors at all.
        window.output_view.enabled.setChecked(True)
        window.output_view.gain.setValue(-3)
        window.output_view.render_button.click()
        wait()
        assert not errors
        assert window.views.currentStep() == "output"
        window.go_continue()
        assert window.views.currentStep() == "export"

        # Export writes the full-chain render, never a preview buffer.
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(export), "")
        )
        assert window.export_view.export_button.isEnabled()
        window.export_view.export_button.click()
        wait()
        written, written_rate = sf.read(export, always_2d=True)
        assert written_rate == rate
        np.testing.assert_allclose(written, window.output[0], atol=1e-6)
        assert not errors
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_keyboard_moves_between_steps_without_touching_playback(tmp_path):
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(268).normal(0, 0.1, (16000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    try:
        window.show()
        window.loaded("source", load_audio(source))
        window.waveform.set_selection(SampleRegion(2000, 12000))
        window.loop_selection.setChecked(True)
        window.position = 5000

        # The offscreen platform never makes the window active, so a real key
        # press cannot reach a shortcut here. Check what the keys are bound to,
        # then drive the binding the platform would deliver.
        assert window.continue_shortcut.key().toString() == "Alt+Right"
        assert window.back_shortcut.key().toString() == "Alt+Left"
        for shortcut in (window.continue_shortcut, window.back_shortcut):
            assert shortcut.context() is QtCore.Qt.ShortcutContext.WindowShortcut

        window.continue_shortcut.activated.emit()
        assert window.views.currentStep() == "repair"
        window.back_shortcut.activated.emit()
        assert window.views.currentStep() == "listen"

        # An unbound key must not move the step.
        QtTest.QTest.keyClick(window, QtCore.Qt.Key.Key_Right)
        assert window.views.currentStep() == "listen"

        # Step context survives navigation: selection, cursor and loop are the
        # transport's, not the step's.
        assert window.waveform.selection == SampleRegion(2000, 12000)
        assert window.position == 5000
        assert window.transport.loop
        assert not errors
    finally:
        window.close()


def test_comparison_button_explains_why_it_is_unavailable(tmp_path):
    """A disabled comparison must say what to do, not look broken."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(230).normal(0, 0.1, (16000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    try:
        window.show()
        assert "load a recording" in window.ab_button.toolTip().lower()
        window.loaded("source", load_audio(source))
        app.processEvents()
        assert "passes unchanged" in window.ab_button.toolTip().lower()
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("original"))
        app.processEvents()
        assert not window.ab_button.isEnabled()
        assert "step input" in window.ab_button.toolTip().lower()
        window.workspace.signal.setCurrentIndex(window.workspace.signal.findData("input"))
        app.processEvents()
        # Returning to step input explains the identity behavior.
        assert not window.ab_button.isEnabled()
        assert "passes unchanged" in window.ab_button.toolTip().lower()
    finally:
        window.close()


def test_shared_status_tracks_targeted_edits_and_clears_final_measurements(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(233).normal(0, 0.15, (32000, 2))
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    def render():
        window.views.setCurrentWidget(window.output_view)
        window.output_view.render_button.click()
        wait()

    try:
        window.show()
        window.loaded("source", load_audio(source))
        window.manual_eq_view.add_band(2200, -4)
        window.compressor_view.enabled.setChecked(True)
        window.output_view.gain.setValue(-3)
        render()

        status = window.workflow_status()
        assert status.stages["eq"].state is RenderState.READY
        assert status.stages["dynamics"].state is RenderState.READY
        assert status.stages["output"].state is RenderState.READY
        assert status.export_available and window.export_button.isEnabled()
        assert "Output RMS" in window.meters.text()

        window.output_view.gain.setValue(-4)
        status = window.workflow_status()
        assert status.stages["eq"].state is RenderState.READY
        assert status.stages["dynamics"].state is RenderState.READY
        assert status.stages["output"].state is RenderState.NEEDS_RENDER
        assert not status.export_available and not window.export_button.isEnabled()
        assert window.meters.text() == "Render to update measurements."
        assert window.output_view.summary.text() == status.stages["output"].reason
        assert window.views.action_label.text() == status.stages["output"].reason
        window.output_view.draw()
        assert "render to measure" in window.output_view.readouts[2].text()

        render()
        window.views.setCurrentWidget(window.compressor_view)
        window.compressor_view.threshold.setValue(-22)
        status = window.workflow_status()
        assert status.stages["eq"].state is RenderState.READY
        assert status.stages["dynamics"].state is RenderState.NEEDS_RENDER
        assert status.stages["output"].state is RenderState.NEEDS_RENDER
        assert not window.export_button.isEnabled()
        assert window.compressor_view.summary.text() == status.stages["dynamics"].reason
        assert window.views.action_label.text() == status.stages["dynamics"].reason

        render()
        window.views.setCurrentWidget(window.manual_eq_view)
        window.manual_eq_view.gain.setValue(-5)
        status = window.workflow_status()
        assert status.stages["eq"].state is RenderState.NEEDS_RENDER
        assert status.stages["dynamics"].state is RenderState.NEEDS_RENDER
        assert status.stages["output"].state is RenderState.NEEDS_RENDER
        assert not window.export_button.isEnabled()
        assert window.manual_eq_view.summary.text() == status.stages["eq"].reason
        assert window.views.action_label.text() == status.stages["eq"].reason
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_analysis_handlers_reject_an_invalid_downstream_recipe():
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(234).normal(0, 0.1, (16000, 2))
    reference = np.random.default_rng(235).normal(0, 0.1, (16000, 2))
    try:
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
        window.loaded("reference", (reference, rate, analyse(reference, rate), "reference"))
        window.project.stages["eq"].bypassed = False
        window.project.stages["eq"].parameters = {"kind": "future-eq"}
        window.update_buttons()

        assert not window.process_button.isEnabled()
        window.process()
        assert errors and "saved eq settings" in errors.pop().lower()
        assert window.worker is None and window.project.match_mode == "none"

        window.section_workbench.sections = [
            SectionAssignment("verse", "Verse", SampleRegion(0, 4000), "target")
        ]
        window.section_workbench.render_all()
        assert errors and "saved eq settings" in errors.pop().lower()
        assert window.worker is None and window.project.match_mode == "none"
    finally:
        window.close()


def test_manual_region_eq_keeps_matching_and_compares_only_the_eq_step(tmp_path, monkeypatch):
    import copy

    import sounddevice as sd

    from omazone.gui import load_audio
    from omazone.project import NamedRegion

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    audio = np.random.default_rng(130).normal(0, 0.1, (32000, 2))
    reference = signal.sosfilt(signal.butter(2, 2500, fs=rate, output="sos"), audio, axis=0)
    source_path, reference_path = tmp_path / "mix.wav", tmp_path / "reference.wav"
    sf.write(source_path, audio, rate, subtype="DOUBLE")
    sf.write(reference_path, reference, rate, subtype="DOUBLE")

    def wait():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window.worker is None
                and window.workspace.job is None
                and not window.workspace.timer.isActive()
            ):
                assert not errors
                return
            time.sleep(0.01)
        raise AssertionError("Render did not finish")

    try:
        window.show()
        window.loaded("source", load_audio(source_path))
        window.loaded("reference", load_audio(reference_path))
        window.process()
        wait()
        matching_output = window.match_after.copy()
        calibration = window.project.calibration
        coefficients = calibration.whole.coefficients.copy()
        matching_preview = window.mastering_preview
        window.project.regions.append(
            NamedRegion("guitar", "Acoustic guitar", SampleRegion(8000, 24000))
        )
        window.refresh_named_regions()
        window.views.setCurrentWidget(window.manual_eq_view)
        eq = window.manual_eq_view
        eq.region.setCurrentIndex(eq.region.findData("guitar"))
        eq.add_button.click()
        eq.frequency.setValue(2200)
        eq.gain.setValue(-4)
        assert eq.enabled.isChecked()
        assert window.output is None
        assert window.mastering_preview is matching_preview
        assert window.project.calibration is calibration and not window.project.needs_reanalysis
        eq.render_button.click()
        wait()
        assert window.output is not None
        np.testing.assert_array_equal(window.match_after, matching_output)
        np.testing.assert_array_equal(window.project.calibration.whole.coefficients, coefficients)
        np.testing.assert_array_equal(window.output[0][:8000], matching_output[:8000])
        np.testing.assert_array_equal(window.output[0][24000:], matching_output[24000:])
        assert np.any(window.output[0][8000:24000] != matching_output[8000:24000])
        assert window.renderer.computations["match"] == 1
        assert window.views.currentWidget() is eq
        # The user's own loop choice survives selecting a comparison.
        window.waveform.set_selection(SampleRegion(8000, 24000))
        window.loop_selection.setChecked(True)
        assert window.transport.loop
        eq.listen_button.click()
        assert window.audition_mode == "eq"
        assert window.transport.loop
        block = np.empty((128, 2), dtype=np.float32)
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, window.eq_preview[0][8000:8128])
        window.toggle_ab()
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, window.eq_preview[1][8128:8256])
        assert window.ab_button.text() == "Listening: after EQ"
        window.stop()

        export = tmp_path / "final.wav"
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(export), "")
        )
        window.export()
        wait()
        saved, saved_rate = sf.read(export, always_2d=True)
        assert saved_rate == rate
        np.testing.assert_allclose(saved, window.output[0], atol=1e-7)
        expected_output = window.output[0].copy()
        expected_parameters = copy.deepcopy(window.project.stages["eq"].parameters)
        project_path = tmp_path / "eq-session.omazone.json"
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(project_path), "")
        )
        window.save_current_project()
        wait()
        window.new_project()
        window.open_project_path(project_path)
        wait()
        assert window.project.stages["eq"].parameters == expected_parameters
        assert eq.region.currentData() == "guitar" and eq.gain.value() == -4
        assert window.output is None
        eq.render_button.click()
        wait()
        np.testing.assert_array_equal(window.output[0], expected_output)
        np.testing.assert_array_equal(window.project.calibration.whole.coefficients, coefficients)
        eq.enabled.setChecked(False)
        eq.render_button.click()
        wait()
        np.testing.assert_array_equal(window.output[0], matching_output)
        assert window.project.stages["eq"].parameters == expected_parameters
        eq.enabled.setChecked(True)
        eq.gain.setValue(-2)
        eq.render_button.click()
        wait()
        assert window.renderer.computations["match"] == 1
        np.testing.assert_array_equal(window.project.calibration.whole.coefficients, coefficients)
        eq_parameters = copy.deepcopy(window.project.stages["eq"].parameters)
        window.amount.setValue(60)
        assert window.project.stages["eq"].parameters == eq_parameters
        window.process()  # Explicitly relearn upstream matching, retaining the later band.
        wait()
        assert window.project.stages["eq"].parameters == eq_parameters
        assert window.eq_preview is not None
        assert window.project.regions[-1].id == "guitar"
        eq_output = window.chain_result.equalized.copy()
        match_computations = window.renderer.computations["match"]
        retained_calibration = window.project.calibration.whole.coefficients.copy()
        comp = window.compressor_view
        window.views.setCurrentWidget(comp)
        comp.threshold.setValue(-27)
        comp.enabled.setChecked(True)
        assert window.output is None and window.eq_preview is not None
        comp.render_button.click()
        wait()
        np.testing.assert_array_equal(window.chain_result.equalized, eq_output)
        assert window.dynamics_preview is not None
        assert window.renderer.computations["match"] == match_computations
        np.testing.assert_array_equal(
            window.project.calibration.whole.coefficients, retained_calibration
        )
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_multiband_canvas_edits_and_shared_viewer_without_relearning(tmp_path):
    from omazone.manual_eq import eq_from_parameters, render_eq

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    audio = np.random.default_rng(145).normal(0, 0.05, (16000, 2))
    path = tmp_path / "song.wav"
    sf.write(path, audio, 16000, subtype="DOUBLE")
    try:
        window.show()
        app.processEvents()
        window.loaded("source", load_audio(path))
        eq = window.manual_eq_view
        window.views.setCurrentWidget(eq)
        assert window.workspace.plot_stack.currentWidget() is eq.canvas
        assert window.workspace.mode.currentData() == "spectrum"
        eq.canvas.placed.emit(170, 2)
        eq.canvas.placed.emit(3200, -4)
        position = eq.canvas.getViewBox().mapViewToScene(QtCore.QPointF(np.log10(800), -2))

        class PlotClick:
            def button(self):
                return QtCore.Qt.MouseButton.LeftButton

            def isAccepted(self):
                return False

            def scenePos(self):
                return position

        eq.canvas.plot_clicked(PlotClick())
        assert len(eq.bands) == 3 and eq.bands[-1].frequency == pytest.approx(800)
        eq.remove_button.click()
        assert len(eq.bands) == len(eq.canvas.handles) == 2
        first, second = eq.canvas.handles
        assert eq.selected == 1 and eq.frequency.value() == 3200
        first.setPos(np.log10(220), 3)
        first.sigPositionChangeFinished.emit(first)
        assert eq.selected == 0
        assert eq.bands[0].frequency == pytest.approx(220)

        class Wheel:
            def delta(self):
                return 120

            def accept(self):
                pass

        first.wheelEvent(Wheel())
        assert eq.q.value() == 1.1
        window.playing = True
        window.transport.position = 12000
        assert window.workspace.spectrum_scope(audio, 16000, None, True) == (0, 11200)
        window.transport.position = 14400
        assert window.workspace.spectrum_scope(audio, 16000, None, True) == (0, 14400)
        window.playing = False
        eq.band_enabled.setChecked(False)
        assert not eq.bands[0].enabled
        eq.duplicate_button.click()
        assert len(eq.bands) == 3
        eq.remove_button.click()
        assert len(eq.bands) == 2
        params = window.project.stages["eq"].parameters
        assert params["kind"] == "multi-bell-v2"
        assert len(eq_from_parameters(params).bands) == 2
        window.render_saved_recipe()
        deadline = time.monotonic() + 10
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and window.output is not None
        np.testing.assert_allclose(
            window.output[0], render_eq(audio, 16000, eq.settings()), atol=1e-12
        )
        eq.enabled.setChecked(False)
        np.testing.assert_allclose(eq.canvas.response.yData, 0)
        window.views.setStep("match")
        assert window.workspace.plot_stack.currentWidget() is window.workspace.spectra
    finally:
        if window.workspace.job:
            window.workspace.close_jobs()
        window.close()


def test_compressor_stage_ab_save_and_bypass(tmp_path, monkeypatch):
    import sounddevice as sd

    from omazone.compressor import render_compressor
    from omazone.manual_eq import render_eq

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    t = np.arange(rate) / rate
    envelope = np.where(t < 0.4, 0.08, 0.8)
    left = envelope * np.sin(2 * np.pi * 440 * t)
    audio = np.column_stack((left, -0.6 * left))
    source = tmp_path / "recording.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    try:
        window.show()
        window.loaded("source", load_audio(source))
        eq = window.manual_eq_view
        eq.add_band(2100, -2)
        comp = window.compressor_view
        window.views.setCurrentWidget(comp)
        assert window.workspace.plot_stack.currentWidget() is comp.canvas
        comp.threshold.setValue(-28)
        comp.ratio.setValue(3)
        comp.enabled.setChecked(True)
        assert not window.project.stages["dynamics"].bypassed
        comp.render_button.click()
        wait()
        before = render_eq(audio, rate, eq.settings())
        direct = render_compressor(before, rate, comp.settings())
        np.testing.assert_allclose(window.output[0], direct.audio, atol=1e-12)
        np.testing.assert_array_equal(window.chain_result.equalized, before)
        assert window.chain_result.compression is not None
        assert len(comp.canvas.reduction_curve.yData) == len(direct.reduction_db)
        window.position = rate // 2
        window.update_transport()
        assert comp.canvas.cursors[0].value() == pytest.approx(0.5)
        assert window.renderer.computations == {
            "repair": 1,
            "match": 1,
            "eq": 1,
            "dynamics": 1,
            "output": 1,
        }
        window.waveform.set_selection(SampleRegion(7000, 12000))
        comp.listen_button.click()
        assert window.audition_mode == "dynamics"
        block = np.empty((128, 2), dtype=np.float32)
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, window.dynamics_preview[0][7000:7128])
        window.toggle_ab()
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, window.dynamics_preview[1][7128:7256])
        assert window.ab_button.text() == "Listening: after compression"
        window.stop()
        before_eq = window.eq_preview[1].copy()
        comp.attack.setValue(30)
        assert window.output is None and window.eq_preview is not None
        comp.render_button.click()
        wait()
        assert window.renderer.computations == {
            "repair": 1,
            "match": 1,
            "eq": 1,
            "dynamics": 2,
            "output": 2,
        }
        np.testing.assert_allclose(window.eq_preview[1], before_eq, atol=1e-12)
        expected = window.output[0].copy()
        project_file = tmp_path / "compressor.omazone.json"
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(project_file), "")
        )
        window.save_current_project()
        wait()
        window.new_project()
        window.open_project_path(project_file)
        wait()
        assert window.compressor_view.attack.value() == 30
        assert window.compressor_view.enabled.isChecked()
        window.render_saved_recipe()
        wait()
        np.testing.assert_array_equal(window.output[0], expected)
        window.compressor_view.enabled.setChecked(False)
        window.render_saved_recipe()
        wait()
        np.testing.assert_array_equal(window.output[0], before)
        assert window.project.stages["dynamics"].parameters["kind"] == "compressor-v1"
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()


def test_output_gain_peaks_export_ab_and_saved_project(tmp_path, monkeypatch):
    import sounddevice as sd

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    errors = []
    window.error = errors.append
    rate = 16000
    t = np.arange(rate) / rate
    audio = (1.2 * np.sin(2 * np.pi * 440 * t))[:, None]
    source = tmp_path / "over-range.wav"
    sf.write(source, audio, rate, subtype="FLOAT")

    def wait():
        deadline = time.monotonic() + 15
        while window.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert window.worker is None and not errors

    try:
        window.show()
        window.loaded("source", load_audio(source))
        view = window.output_view
        window.views.setCurrentWidget(view)
        assert window.workspace.plot_stack.currentWidget() is view.canvas
        assert "OVER 0 dBFS" in view.readouts[0].text()
        assert "render to measure" in view.readouts[2].text()
        view.gain.setValue(-6)
        assert view.enabled.isChecked()
        assert window.project.stages["output"].parameters["kind"] == "output-gain-v1"
        view.render_button.click()
        wait()
        source_audio = window.source[0]
        np.testing.assert_allclose(window.output[0], source_audio * 10 ** (-6 / 20))
        assert window.chain_result.pre_output is source_audio
        assert "OVER 0 dBFS" in view.readouts[0].text()
        assert "OVER 0 dBFS" in view.readouts[1].text()
        assert "OVER 0 dBFS" not in view.readouts[2].text()
        assert window.renderer.computations == {
            "repair": 1,
            "match": 1,
            "eq": 1,
            "dynamics": 1,
            "output": 1,
        }
        window.waveform.set_selection(SampleRegion(4000, 10000))
        view.listen_button.click()
        assert window.audition_mode == "output-gain"
        block = np.empty((128, 1), dtype=np.float32)
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, window.output_preview[0][4000:4128])
        window.toggle_ab()
        window.stream.callback(block, 128, None, None)
        np.testing.assert_array_equal(block, window.output_preview[1][4128:4256])
        assert window.ab_button.text() == "Listening: after output gain"
        window.stop()
        exported = tmp_path / "master.wav"
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(exported), "")
        )
        window.export()
        wait()
        saved_audio, saved_rate = sf.read(exported, always_2d=True)
        assert saved_rate == rate and sf.info(exported).subtype == "FLOAT"
        np.testing.assert_allclose(saved_audio, window.output[0], atol=1e-7)
        expected = window.output[0].copy()
        saved_recipe = tmp_path / "output.omazone.json"
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(saved_recipe), "")
        )
        window.save_current_project()
        wait()
        window.new_project()
        window.open_project_path(saved_recipe)
        wait()
        assert view.gain.value() == -6 and view.enabled.isChecked()
        window.render_saved_recipe()
        wait()
        np.testing.assert_allclose(window.output[0], expected, atol=1e-12)
        view.gain.setValue(6)
        assert window.output is None and window.dynamics_preview is not None
        assert not window.export_button.isEnabled()
        assert "render to measure" in view.readouts[2].text()
        window.render_saved_recipe()
        wait()
        assert window.renderer.computations["output"] == 2
        assert window.renderer.computations["dynamics"] == 1
        assert "OVER 0 dBFS" in view.readouts[2].text()
        view.enabled.setChecked(False)
        window.render_saved_recipe()
        wait()
        np.testing.assert_array_equal(window.output[0], window.source[0])
        window.project.stages["output"].parameters = {"ceiling_db": -1}
        view.restore()
        view.gain.setValue(4)
        assert view.gain.value() == 0
        assert window.project.stages["output"].parameters == {"ceiling_db": -1}
        view.enabled.setChecked(True)
        assert not window.project.stages["output"].bypassed
        assert window.project.stages["output"].parameters == {"ceiling_db": -1}
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()
