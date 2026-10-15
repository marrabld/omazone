import os
import time

# Geometry checks must not inherit desktop tiling/window rules.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
import soundfile as sf
from PySide6 import QtCore, QtWidgets

from omazone.engine import analyse
from omazone.gui import Window, load_audio
from omazone.project import NamedRegion
from omazone.sections import capture_target
from omazone.waveform import SampleRegion
from omazone.workflow_steps import KEYS


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
        window.workspace.tool_modes.update({key: "both" for key in KEYS[: window.views.count()]})
        window.workspace.overview_action.setChecked(True)
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        for key in KEYS[: window.views.count()]:
            window.views.setStep(key)
            app.processEvents()
            assert window.waveform.isVisible()
            assert window.workspace.overview.isVisible()
            assert window.waveform.selection == selection
            np.testing.assert_allclose(window.waveform.channel_plots[0].viewRange()[0], [0.4, 1.3])
            assert window.position == 9000
            assert window.transport.region == selection and window.transport.loop
            assert window.workspace.mode.currentData() == "both"
        window.views.setStep("reference")
        window.section_workbench.reference_waveform.select_seconds(0.1, 0.4)
        assert window.waveform.selection == selection
        assert window.position == 9000
        assert window.workspace.reference_pane.isVisible()
        assert len(window.workspace.overview_regions) == 2
        assert any(
            hasattr(item, "toPlainText") and item.toPlainText() == "Guitar"
            for item in window.workspace.overview_regions
        )
        wait_jobs(app, window)
    finally:
        window.close()


def test_task_view_defaults_and_overrides_are_remembered():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    try:
        window.show()
        # The opening step marks passages, so the viewer starts on the waveform.
        assert window.workspace.mode.currentData() == "waveform"
        assert window.workspace.view_controls.isHidden()
        assert window.playback_selection_controls.isHidden()
        assert window.match_advanced_panel.isHidden()
        for step in ("listen", "reference", "sections", "repair"):
            window.views.setStep(step)
            assert window.workspace.mode.currentData() == "waveform"
        window.views.setStep("listen")
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        window.views.setStep("match")
        assert window.workspace.mode.currentData() == "spectrum"
        window.workspace.mode_actions["waveform"].trigger()
        window.views.setStep("listen")
        assert window.workspace.mode.currentData() == "both"
        window.views.setStep("match")
        assert window.workspace.mode.currentData() == "waveform"
        # Preferences are keyed by step name, so navigation can be reordered.
        assert window.workspace.preferences()["viewer_tool_modes"]["listen"] == "both"
    finally:
        app.processEvents()
        window.close()


def test_matching_has_usable_plot_area_and_bounded_linked_frequency_axes():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 48000
    audio = np.random.default_rng(97).normal(0, 0.1, (rate, 2))
    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
        window.loaded("reference", (audio * 0.5, rate, analyse(audio * 0.5, rate), "reference"))
        window.views.setStep("match")
        for width, height in ((1024, 768), (1280, 900)):
            window.resize(width, height)
            wait_jobs(app, window)
            assert window.workspace_split.orientation() == QtCore.Qt.Orientation.Horizontal
            assert window.spectrum_plot.getViewBox().height() >= 140
            assert window.eq_plot.getViewBox().height() >= 80
            assert window.workspace.width() > window.width() * 0.65
            assert window.process_button.isVisible()
        assert window.height() <= height
        window.waveform.set_selection(SampleRegion(1000, 10000))
        window.workspace.loop_action.setChecked(True)
        assert window.transport.loop
        assert "Loop active" in window.workspace.label.text()
        window.whole_song()
        assert not window.workspace.loop_action.isChecked()
        window.eq_plot.getViewBox().setXRange(-10, 14, padding=0)
        app.processEvents()
        for plot in (window.spectrum_plot, window.eq_plot):
            low, high = plot.viewRange()[0]
            assert low >= np.log10(20) - 1e-6
            assert high <= np.log10(20000) + 1e-6
            assert plot.getAxis("bottom").logMode
            assert not plot.getAxis("bottom").autoSIPrefix
        window.process()
        wait_jobs(app, window)
        assert len(window.eq_plot.listDataItems()) == 2
        window.amount.setValue(60)
        assert (
            len(window.eq_plot.listDataItems()) == 1
        )  # Honest unity placeholder, not stale correction.
        assert "No correction" in window.eq_plot.plotItem.titleLabel.text
        np.testing.assert_allclose(
            window.eq_plot.viewRange()[0], window.spectrum_plot.viewRange()[0]
        )
        window.resize(850, 850)
        app.processEvents()
        assert window.workspace_split.orientation() == QtCore.Qt.Orientation.Vertical
        assert window.process_button.isVisible()
    finally:
        window.close()


def test_matching_plots_remain_readable_at_laptop_and_stacked_sizes():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(70).normal(0, 0.05, (rate, 2))
    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
        window.views.setStep("match")
        wait_jobs(app, window)
        for width, height in ((1366, 768), (1280, 800), (949, 768), (900, 768), (820, 560)):
            window.resize(width, height)
            for _ in range(10):
                app.processEvents()
            # Assert actual geometry too: a forced larger window must not make
            # these viewbox tests pass while lying about laptop compatibility.
            assert window.width() == width and window.height() == height
            assert window.views.navigationFits()
            assert window.tool_scroll.horizontalScrollBar().maximum() == 0
            for plot, minimum in ((window.spectrum_plot, 100), (window.eq_plot, 80)):
                view = plot.getViewBox()
                assert view.height() >= minimum - 1
                assert view.height() >= plot.getAxis("left").label.boundingRect().width() - 1
                assert plot.isVisible()
            assert not window.spectrum_plot.getAxis("bottom").isVisible()
            assert window.eq_plot.getAxis("bottom").isVisible()
            scroll = window.workspace_scroll
            if height >= 768:
                assert scroll.verticalScrollBar().maximum() == 0
            else:
                assert scroll.verticalScrollBar().maximum() > 0
                # Overflow is explicit and reachable, not a plot drawn outside
                # its viewport with no means of reaching its labels.
                scroll.ensureWidgetVisible(window.eq_plot)
                app.processEvents()
                assert scroll.verticalScrollBar().value() > 0
            assert window.workspace.height() < 800, "Do not scroll pyqtgraph's 1500px preferred scene"
    finally:
        window.close()


def test_wrapped_workspace_warning_gets_space_without_squeezing_plots():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    try:
        window.show()
        window.views.setStep("match")
        window.resize(900, 700)
        badge = window.workspace.badge
        badge.setText("The selected output needs rendering before you can compare it. " * 12)
        badge.show()
        for _ in range(10):
            app.processEvents()
        assert badge.height() >= badge.heightForWidth(badge.width())
        assert window.workspace_scroll.verticalScrollBar().maximum() > 0
        assert window.spectrum_plot.getViewBox().height() >= 99
        assert window.eq_plot.getViewBox().height() >= 79
    finally:
        window.close()


def test_matching_plot_minima_account_for_larger_axis_fonts():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    try:
        window.show()
        window.views.setStep("match")
        window.resize(900, 768)
        for plot in (window.spectrum_plot, window.eq_plot):
            axis = plot.getAxis("left")
            font = axis.label.font()
            font.setPointSize(18)
            axis.label.setFont(font)
            plot.getAxis("bottom").setStyle(tickFont=font)
        for _ in range(10):
            app.processEvents()
        for plot, minimum in ((window.spectrum_plot, 100), (window.eq_plot, 80)):
            window.workspace.protect_plot_height(plot, minimum)
        for _ in range(10):
            app.processEvents()
        for plot in (window.spectrum_plot, window.eq_plot):
            assert plot.getViewBox().height() >= plot.getAxis("left").label.boundingRect().width() - 1
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
        window.views.setStep("match")
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
        window.workspace.overview_action.setChecked(True)
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
        window.views.setStep("reference")
        window.section_workbench.target_list.setCurrentRow(0)
        app.processEvents()
        assert window.workspace.reference_pane.isVisible()
        assert window.workspace.target_plot.isVisible()
        assert not window.section_workbench.reference_waveform.isVisible()
        assert "Saved clean target" in window.workspace.reference_label.text()
        assert window.waveform.isVisible()
        window.workspace.mode.setCurrentIndex(window.workspace.mode.findData("both"))
        window.workspace.tool_modes["4"] = "spectrum"
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
        assert window.workspace.tool_modes["4"] == "spectrum"
        assert window.waveform.selection == SampleRegion(2000, 10000)
        np.testing.assert_allclose(window.waveform.channel_plots[0].viewRange()[0], [0.1, 0.8])
        assert len(window.project.targets) == 1
    finally:
        window.close()


def test_file_loading_never_changes_tool_or_view_and_reference_keeps_mix_context():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(94).normal(0, 0.1, (32000, 2))
    reference = audio[:16000]
    try:
        window.show()
        assert window.views.currentStep() == "listen"
        assert window.workspace.mode.currentData() == "waveform"
        assert window.views.action_label.text() == "Load a mix."
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
        assert window.views.currentStep() == "listen"
        window.views.setStep("match")
        assert window.views.currentStep() == "match"
        assert (
            window.views.action_label.text()
            == "Matching is optional. Add a reference to analyse it; otherwise it passes unchanged."
        )
        window.loaded("reference", (reference, rate, analyse(reference, rate), "reference"))
        assert window.views.currentStep() == "match"
        # The wording names the alternative route, because "Analyse Matching"
        # read as though whole-song matching were the only kind.
        assert (
            window.views.action_label.text()
            == "Mix and reference ready. Analyse the whole song, or use Sections "
            "to match parts separately."
        )
        assert window.process_button.isEnabled()

        for mode in ("waveform", "spectrum", "both"):
            window.workspace.mode.setCurrentIndex(window.workspace.mode.findData(mode))
            for key in KEYS[: window.views.count()]:
                window.views.setStep(key)
                window.workspace.mode.setCurrentIndex(window.workspace.mode.findData(mode))
                window.loaded("source", (audio.copy(), rate, analyse(audio, rate), "new mix"))
                assert window.views.currentStep() == key
                assert window.workspace.mode.currentData() == mode
                selection = SampleRegion(8000, 20000)
                window.waveform.set_selection(selection)
                window.waveform.channel_plots[0].setXRange(0.4, 1.3, padding=0)
                window.position = 9000
                window.loop_selection.setChecked(True)
                window.loaded(
                    "reference", (reference.copy(), rate, analyse(reference, rate), "new reference")
                )
                assert window.views.currentStep() == key
                assert window.workspace.mode.currentData() == mode
                assert window.waveform.selection == selection
                assert window.position == 9000
                assert window.transport.region == selection and window.transport.loop
                np.testing.assert_allclose(
                    window.waveform.channel_plots[0].viewRange()[0], [0.4, 1.3]
                )
        wait_jobs(app, window)
    finally:
        window.close()


def test_primary_actions_and_navigation_remain_visible_when_settings_scroll():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    audio = np.random.default_rng(95).normal(0, 0.1, (16000, 1))

    def assert_visible(widget):
        assert widget.isVisible()
        top_left = widget.mapTo(window, QtCore.QPoint(0, 0))
        assert window.rect().contains(QtCore.QRect(top_left, widget.size()))
        assert not window.tool_scroll.isAncestorOf(widget)

    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
        window.loaded("reference", (audio, rate, analyse(audio, rate), "reference"))
        for width, height in ((1024, 768), (1280, 900)):
            window.resize(width, height)
            for mode in ("waveform", "spectrum", "both"):
                window.workspace.mode.setCurrentIndex(window.workspace.mode.findData(mode))
                for page, action in (
                    (window.match_page, window.process_button),
                    (window.section_workbench, window.section_workbench.render_button),
                ):
                    window.views.setCurrentWidget(page)
                    window.workspace.mode.setCurrentIndex(window.workspace.mode.findData(mode))
                    window.workspace_split.setSizes([10000, 165])
                    app.processEvents()
                    scroll = window.tool_scroll.verticalScrollBar()
                    scroll.setValue(scroll.maximum())
                    app.processEvents()
                    assert_visible(window.views.navigation)
                    assert_visible(action)
                    assert_visible(window.views.action_label)
                    assert window.width() <= width
                    assert window.height() <= height
        window.views.setCurrentWidget(window.clipping_inspector)
        assert window.views.action_bar.isVisible()
        assert window.clipping_inspector.analyse_button.isVisible()
        assert window.process_button.isHidden()
    finally:
        window.close()


def test_all_tool_panels_prioritise_drawable_area_without_horizontal_scrolling():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    rate = 16000
    t = np.arange(rate * 2) / rate
    clean = 0.7 * np.sin(2 * np.pi * 440 * t)
    audio = np.column_stack((np.clip(clean, -0.35, 0.45), clean * 0.4))
    reference = np.column_stack((clean, clean * 0.8))
    try:
        window.show()
        window.loaded("source", (audio, rate, analyse(audio, rate), "mix"))
        window.loaded("reference", (reference, rate, analyse(reference, rate), "reference"))
        window.waveform.set_selection(SampleRegion(4000, 20000))
        workbench = window.section_workbench
        workbench.target_captured(
            capture_target(reference, rate, SampleRegion(0, 16000), "Clean", "clean", "reference")
        )
        workbench.use_mix_selection()
        workbench.add_section()
        for width, height in ((1024, 768), (1280, 900)):
            window.resize(width, height)
            for key in KEYS[: window.views.count()]:
                window.views.setStep(key)
                app.processEvents()
                assert window.workspace_split.orientation() == QtCore.Qt.Orientation.Horizontal
                assert window.workspace.width() >= window.width() * 0.62
                assert window.views.action_bar.isVisible()
                assert window.preview_mode.isHidden()
                assert window.playback_selection_controls.isHidden()
                assert window.tool_scroll.horizontalScrollBar().maximum() == 0
                assert window.height() <= height
                if key in ("listen", "reference", "sections", "repair"):
                    assert window.waveform.isVisible()
                    for plot in window.waveform.channel_plots:
                        assert plot.getViewBox().height() >= 90
                if key == "eq":
                    assert window.manual_eq_view.canvas.isVisible()
                    assert window.manual_eq_view.render_button.isVisible()
                if key == "dynamics":
                    assert window.compressor_view.canvas.isVisible()
                    assert window.compressor_view.render_button.isVisible()
                if key == "output":
                    assert window.output_view.canvas.isVisible()
                    assert window.output_view.render_button.isVisible()
                if key == "reference":
                    assert window.workspace.reference_pane.isVisible()
                    for plot in workbench.reference_waveform.channel_plots:
                        assert plot.getViewBox().height() >= 90
            window.views.setStep("sections")
            old_heights = [plot.getViewBox().height() for plot in window.waveform.channel_plots]
            workbench.advanced_toggle.setChecked(True)
            app.processEvents()
            assert window.tool_scroll.horizontalScrollBar().maximum() == 0
            assert all(
                plot.getViewBox().height() >= old - 1
                for plot, old in zip(window.waveform.channel_plots, old_heights, strict=True)
            )
            workbench.advanced_toggle.setChecked(False)
        window.views.setStep("repair")
        window.clipping_inspector.find_peaks()
        wait_jobs(app, window)
        inspector = window.clipping_inspector
        inspector.review_button.click()
        inspector.check_shown(True)
        window.update_buttons()
        assert inspector.repair_button.isVisible()
        assert not inspector.analyse_button.isVisible()
        assert window.tool_scroll.horizontalScrollBar().maximum() == 0
    finally:
        window.close()


def test_regions_and_reference_capture_use_fixed_actions_and_keep_mix_selection(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    audio = np.random.default_rng(98).normal(0, 0.1, (16000, 1))
    source = tmp_path / "mix.wav"
    reference = tmp_path / "reference.wav"
    sf.write(source, audio, 16000, subtype="DOUBLE")
    sf.write(reference, audio, 16000, subtype="DOUBLE")
    try:
        window.show()
        window.loaded("source", load_audio(source))
        window.loaded("reference", load_audio(reference))
        window.views.setCurrentWidget(window.region_page)
        selection = SampleRegion(2000, 10000)
        window.waveform.set_selection(selection)
        window.region_name.setText("Acoustic verse")
        window.name_region_button.click()
        assert window.project.regions[-1].name == "Acoustic verse"
        assert window.region_list.item(0).text() == "Acoustic verse"
        assert window.region_advanced_panel.isHidden()
        window.views.setCurrentWidget(window.section_workbench.reference_page)
        assert not window.section_workbench.capture_button.isEnabled()
        window.section_workbench.reference_waveform.select_seconds(0.1, 0.8)
        window.section_workbench.target_name.setText("Clean reference")
        assert window.section_workbench.capture_button.isEnabled()
        window.section_workbench.capture_button.click()
        wait_jobs(app, window)
        assert len(window.project.targets) == 1
        assert window.waveform.selection == selection
        assert window.section_workbench.reference_advanced_panel.isHidden()
        assert not window.tool_scroll.isAncestorOf(window.section_workbench.capture_button)
    finally:
        if window.worker:
            window.worker.wait()
            app.processEvents()
        window.close()
