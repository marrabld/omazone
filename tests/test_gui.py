"""Exercise worker handoff, plot updates, and float-WAV export without a display."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtCore, QtTest, QtWidgets
from scipy import signal

from omazone.engine import analyse
from omazone.gui import Window, load_audio
from omazone.waveform import PeakIndex, SampleRegion


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
        assert window.playing and window.transport.loop
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
        window.views.setCurrentIndex(0)
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
        window.render_saved_recipe()
        wait()
        np.testing.assert_allclose(window.output[0], expected, atol=1e-12)
        np.testing.assert_array_equal(
            window.project.calibration.sections[0].filter.coefficients, coefficients
        )
        window.set_stage_bypass("repair", True)
        assert window.processing_source() is window.source
        assert window.project.needs_reanalysis and window.project.can_render_saved_match
        assert window.project.repairs and workbench.sections
        window.render_saved_recipe()
        wait()
        np.testing.assert_array_equal(
            window.project.calibration.sections[0].filter.coefficients, coefficients
        )
        assert window.project.needs_reanalysis  # Saved rendering did not silently relearn.

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
        data["view"]["active_tool"] = "not a tool"
        file.write_text(json.dumps(data))
        window.open_project_path(file)
        wait()
        assert errors and "tool" in errors[0]
        assert window.project is original
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
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
        matching_output = window.match_output.copy()
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
        eq.frequency.setValue(2200)
        eq.gain.setValue(-4)
        assert eq.enabled.isChecked()
        assert window.output is None
        assert window.mastering_preview is matching_preview
        assert window.project.calibration is calibration and not window.project.needs_reanalysis
        eq.render_button.click()
        wait()
        assert window.output is not None
        np.testing.assert_array_equal(window.match_output, matching_output)
        np.testing.assert_array_equal(window.project.calibration.whole.coefficients, coefficients)
        np.testing.assert_array_equal(window.output[0][:8000], matching_output[:8000])
        np.testing.assert_array_equal(window.output[0][24000:], matching_output[24000:])
        assert np.any(window.output[0][8000:24000] != matching_output[8000:24000])
        assert window.renderer.computations["match"] == 1
        assert window.views.currentWidget() is eq
        eq.listen_button.click()
        assert window.audition_mode == "eq" and window.transport.loop
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
    finally:
        if window.worker is not None:
            window.worker.wait()
            app.processEvents()
        window.close()
