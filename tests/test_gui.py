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
        assert window.views.currentWidget() is window.waveform
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
