"""Persistent song context around tool controls, with independent references."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .engine import analyse
from .waveform import PeakIndex


class SpectrumTask(QtCore.QThread):
    ready = QtCore.Signal(object)

    def __init__(self, key, before, after, rate, region):
        super().__init__()
        self.key, self.before, self.after, self.rate, self.region = key, before, after, rate, region

    def run(self):
        try:
            start, end = self.region
            before = analyse(self.before[start:end], self.rate)
            after = analyse(self.after[start:end], self.rate) if self.after is not None else None
            self.ready.emit((self.key, before, after, None))
        except ValueError as error:
            self.ready.emit((self.key, None, None, str(error)))


class SongWorkspace(QtWidgets.QWidget):
    def __init__(self, owner, waveform, spectra, reference):
        super().__init__()
        self.owner, self.waveform, self.reference_waveform = owner, waveform, reference
        waveform.setToolTip(waveform.help_text.text())
        waveform.help_text.hide()
        reference.setToolTip(reference.help_text.text())
        reference.help_text.hide()
        self.syncing = False
        self.refreshing = False
        self.closing = False
        self.wave_key = None
        self.overview_key = None
        self.overview_regions = []
        self.job = None
        self.requested_key = None
        self.completed_key = None
        self.peak_cache = []
        self.pending_key = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.overview = pg.PlotWidget()
        self.overview.setFixedHeight(70)
        self.overview.hideAxis("left")
        self.overview.getAxis("bottom").setHeight(32)
        self.overview.setLabel("bottom", "Song time", units="s")
        self.overview.getViewBox().setMouseEnabled(x=False, y=False)
        self.overview_curve = self.overview.plot(pen=pg.mkPen("#647b9a"))
        self.overview_selection = pg.LinearRegionItem(
            values=(0, 0), brush=pg.mkBrush(99, 223, 192, 35)
        )
        self.overview_selection.sigRegionChangeFinished.connect(self.overview_selected)
        self.overview.addItem(self.overview_selection)
        self.overview_selection.hide()
        self.overview_cursor = pg.InfiniteLine(pos=0, pen=pg.mkPen("#ffffff"))
        self.overview.addItem(self.overview_cursor)
        layout.addWidget(self.overview)
        row = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QComboBox()
        for title, key in (("Waveform", "waveform"), ("Spectrum", "spectrum"), ("Both", "both")):
            self.mode.addItem(title, key)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        row.addWidget(self.mode)
        self.signal = QtWidgets.QComboBox()
        for title, key in (
            ("Step input", "input"),
            ("Step output", "output"),
            ("Original recording", "original"),
        ):
            self.signal.addItem(title, key)
        self.signal.currentIndexChanged.connect(self.signal_changed)
        row.addWidget(self.signal)
        for title, callback in (
            ("Fit song", waveform.fit_song),
            ("Zoom selection", waveform.zoom_selection),
            ("Clear selection", lambda: waveform.set_selection(None)),
        ):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.label = QtWidgets.QLabel("Load a recording to see its waveform.")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.badge = QtWidgets.QLabel()
        self.badge.setWordWrap(True)
        self.badge.setStyleSheet("color: #eabb6b;")
        layout.addWidget(self.badge)
        self.panes = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        mix = QtWidgets.QWidget()
        mix_layout = QtWidgets.QVBoxLayout(mix)
        mix_layout.setContentsMargins(0, 0, 0, 0)
        self.mix_detail = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.mix_detail.addWidget(waveform)
        self.mix_detail.addWidget(spectra)
        waveform.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Expanding
        )
        spectra.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Expanding
        )
        self.spectra = spectra
        mix_layout.addWidget(self.mix_detail)
        self.panes.addWidget(mix)
        self.reference_pane = QtWidgets.QWidget()
        reference_layout = QtWidgets.QVBoxLayout(self.reference_pane)
        reference_layout.setContentsMargins(4, 0, 0, 0)
        self.reference_label = QtWidgets.QLabel("Reference")
        self.reference_label.setWordWrap(True)
        reference_layout.addWidget(self.reference_label)
        self.loaded_reference_button = QtWidgets.QPushButton("Show loaded reference for capture")
        self.loaded_reference_button.clicked.connect(self.show_loaded_reference)
        reference_layout.addWidget(self.loaded_reference_button)
        reference_layout.addWidget(reference)
        self.target_plot = pg.PlotWidget()
        self.target_plot.setLogMode(x=True)
        self.target_plot.setLabel("bottom", "Frequency", units="Hz")
        self.target_plot.setLabel("left", "Saved target", units="dB")
        reference_layout.addWidget(self.target_plot)
        self.panes.addWidget(self.reference_pane)
        self.panes.setSizes([700, 420])
        self.reference_pane.hide()
        layout.addWidget(self.panes, 1)
        self.setMinimumHeight(270)
        self.panes.setChildrenCollapsible(False)
        self.mix_detail.setChildrenCollapsible(False)
        owner.spectrum_plot.setMinimumHeight(80)
        owner.eq_plot.setMinimumHeight(70)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.request_spectrum)
        self.waveform.selection_changed.connect(lambda _: self.refresh())
        self.reference_waveform.selection_changed.connect(lambda _: self.refresh_reference())
        owner.section_workbench.target_list.itemSelectionChanged.connect(self.refresh_reference)
        self.mode_changed()

    def preferences(self):
        return {
            "viewer_mode": self.mode.currentData(),
            "viewer_signal": self.signal.currentData(),
            "viewer_split": self.mix_detail.sizes(),
            "reference_split": self.panes.sizes(),
        }

    def restore_preferences(self, view):
        self.syncing = True
        try:
            self.mode.setCurrentIndex(
                max(0, self.mode.findData(view.get("viewer_mode", "waveform")))
            )
            self.signal.setCurrentIndex(
                max(0, self.signal.findData(view.get("viewer_signal", "input")))
            )
            if view.get("viewer_split"):
                self.mix_detail.setSizes(view["viewer_split"])
            if view.get("reference_split"):
                self.panes.setSizes(view["reference_split"])
        finally:
            self.syncing = False
        self.mode_changed()

    def mode_changed(self):
        mode = self.mode.currentData()
        self.mix_detail.setOrientation(
            QtCore.Qt.Orientation.Horizontal if mode == "both" else QtCore.Qt.Orientation.Vertical
        )
        if mode == "both" and not self.syncing:
            self.mix_detail.setSizes([max(1, self.width() // 2), max(1, self.width() // 2)])
        self.waveform.setVisible(mode != "spectrum")
        self.spectra.setVisible(mode != "waveform")
        self.refresh()
        if mode != "waveform":
            self.timer.start()

    def pairs(self):
        if self.owner.source is None:
            return None, None, 1, "Recording unavailable"
        rate = self.owner.source[1]
        if self.owner.views.currentIndex() == 4:
            before = self.owner.source[0]
            after = self.owner.repair_result.audio if self.owner.repair_result else None
            title = "Repair" + (
                " preview (stage skipped)" if self.owner.project.stages["repair"].bypassed else ""
            )
        else:
            before = self.owner.processing_source()[0]
            after = self.owner.output[0] if self.owner.output is not None else None
            title = (
                "Section matching" if self.owner.project.match_mode == "sections" else "Matching"
            )
            if self.owner.project.stages["match"].bypassed:
                title += " (stage skipped)"
        return before, after, rate, title

    def reference_target(self):
        workbench = self.owner.section_workbench
        if self.owner.views.currentIndex() == 3:
            section = workbench.selected_section()
            if section:
                return workbench.targets[section.target_id]
        if self.owner.views.currentIndex() == 2:
            item = workbench.target_list.currentItem()
            if item:
                return workbench.targets[item.data(QtCore.Qt.ItemDataRole.UserRole)]
        return self.owner.project.reference_target

    def signal_changed(self):
        if not self.syncing:
            self.sync_audio()
        self.refresh()

    def sync_audio(self):
        if self.syncing or self.owner.source is None:
            return
        before, after, _, _ = self.pairs()
        chosen = self.signal.currentData()
        clipping = self.owner.views.currentIndex() == 4
        if chosen == "original":
            mode, processed = 2, False
        elif clipping:
            mode = 1 if self.owner.repair_preview is not None else 2
            processed = chosen == "output" and after is not None
        else:
            mode, processed = 0, chosen == "output" and after is not None
        self.syncing = True
        try:
            self.owner.preview_mode.setCurrentIndex(mode)
            self.owner.listen_processed = processed
            self.owner.update_ab_label()
        finally:
            self.syncing = False

    def follow_audition(self):
        if self.syncing:
            return
        key = (
            "original"
            if self.owner.audition_mode == "original"
            else ("output" if self.owner.listen_processed else "input")
        )
        self.syncing = True
        try:
            self.signal.setCurrentIndex(self.signal.findData(key))
        finally:
            self.syncing = False
        self.refresh()

    def render_completed(self):
        self.sync_audio()
        self.refresh()
        self.completed_key = None
        if self.mode.currentData() != "waveform":
            self.timer.start()

    def refresh(self):
        if self.refreshing or self.closing:
            return
        self.refreshing = True
        try:
            self.refresh_overview()
            before, after, rate, title = self.pairs()
            requested = self.signal.currentData()
            data = (
                self.owner.source[0]
                if requested == "original" and self.owner.source
                else (after if requested == "output" and after is not None else before)
            )
            messages = []
            if requested == "output" and after is None and data is not None:
                messages.append(
                    "Output has not been rendered. Showing step input to retain context."
                )
            if data is None:
                self.wave_key = None
                self.peak_cache = []
                self.owner.spectrum_plot.clear()
                self.owner.eq_plot.clear()
                self.label.setText(
                    "Recording unavailable. Project regions and choices are retained; relink the original."
                )
                self.badge.setText(" | ".join(self.owner.asset_messages))
            else:
                selection = self.waveform.selection
                scope = (
                    f"Selected passage {selection.start / rate:.3f}-{selection.end / rate:.3f} s"
                    if selection
                    else "Whole recording"
                )
                shown = (
                    "original recording"
                    if requested == "original"
                    else ("output" if requested == "output" and after is not None else "input")
                )
                self.label.setText(
                    f"Viewing: {title} {shown} | {scope} | Raw processing levels; audition gain is separate"
                )
                key = (id(data), rate)
                if key != self.wave_key:
                    old_selection = selection
                    zoom = (
                        self.waveform.channel_plots[0].viewRange()[0]
                        if self.waveform.channel_plots
                        else self.owner.project.view.get("zoom")
                    )
                    with QtCore.QSignalBlocker(self.waveform):
                        index = next(
                            (index for audio, index in self.peak_cache if audio is data), None
                        )
                        if index is None:
                            index = (
                                self.owner.source[4]
                                if data is self.owner.source[0] and len(self.owner.source) > 4
                                else PeakIndex(data)
                            )
                            self.peak_cache = [(data, index), *self.peak_cache[:2]]
                        self.waveform.set_audio(index, rate)
                        for plot in self.waveform.channel_plots:
                            plot.setMinimumHeight(65)
                        self.waveform.set_selection(old_selection)
                        if zoom:
                            self.waveform.channel_plots[0].setXRange(*zoom, padding=0)
                    self.wave_key = key
                    self.owner.section_workbench.refresh_overlays()
                    self.waveform.set_clipping(self.owner.clipping_inspector.report)
                    self.waveform.set_repair(
                        self.owner.repair_result if data is self.owner.source[0] else None
                    )
                self.waveform.set_position(self.owner.position)
                for plot in self.waveform.channel_plots:
                    plot.setMinimumHeight(65)
                if self.owner.project.calibration and self.owner.project.needs_reanalysis:
                    messages.append(
                        "Matching calibration needs refresh; saved correction is retained."
                    )
                self.badge.setText(" | ".join(messages))
                if self.mode.currentData() != "waveform":
                    self.timer.start()
            self.refresh_reference()
            editable = self.owner.worker is None and self.owner.source is not None
            self.waveform.setEnabled(editable)
            self.overview.setEnabled(editable)
            self.reference_waveform.setEnabled(
                self.owner.worker is None and self.owner.reference is not None
            )
        finally:
            self.refreshing = False

    def refresh_overview(self):
        source = self.owner.source
        asset = self.owner.project.source
        key = (
            id(source[0]) if source else None,
            tuple(
                (item.id, item.name, item.bounds.start, item.bounds.end)
                for item in self.owner.project.regions
            ),
        )
        rate = source[1] if source else (asset.sample_rate if asset else 1)
        length = len(source[0]) if source else (asset.frames if asset else 1)
        if key != self.overview_key:
            self.overview_curve.clear()
            if source:
                index = source[4] if len(source) > 4 else PeakIndex(source[0])
                samples, low, high = index.visible(0, length, 1000)
                self.overview_curve.setData(
                    np.repeat(samples / rate, 2),
                    np.column_stack((np.min(low, axis=1), np.max(high, axis=1))).ravel(),
                    connect="pairs",
                )
            self.overview.setXRange(0, length / rate, padding=0)
            for item in self.overview_regions:
                self.overview.removeItem(item)
            self.overview_regions = []
            for region in self.owner.project.regions:
                item = pg.LinearRegionItem(
                    values=(region.bounds.start / rate, region.bounds.end / rate),
                    movable=False,
                    brush=pg.mkBrush(115, 168, 255, 18),
                    pen=pg.mkPen("#73a8ff"),
                )
                item.setZValue(-5)
                item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                self.overview.addItem(item)
                label = pg.TextItem(region.name, anchor=(0.5, 0), color="#b8c4d6")
                label.setPos((region.bounds.start + region.bounds.end) / (2 * rate), 1)
                self.overview.addItem(label)
                self.overview_regions.extend((item, label))
            self.overview_selection.setBounds((0, length / rate))
            self.overview_key = key
        selection = self.waveform.selection
        self.syncing = True
        try:
            if selection:
                self.overview_selection.setRegion((selection.start / rate, selection.end / rate))
            self.overview_selection.setVisible(selection is not None)
        finally:
            self.syncing = False
        self.overview_cursor.setValue(self.owner.position / rate)

    def overview_selected(self):
        if self.syncing or self.owner.source is None:
            return
        self.waveform.select_seconds(*self.overview_selection.getRegion())

    def refresh_reference(self):
        visible = self.owner.views.currentIndex() == 2
        self.reference_pane.setVisible(visible)
        if not visible:
            return
        item = self.owner.section_workbench.target_list.currentItem()
        profile = self.reference_target()
        loaded = self.owner.reference
        profile_only = loaded is None or item is not None
        self.loaded_reference_button.setVisible(loaded is not None and item is not None)
        self.reference_waveform.setVisible(not profile_only)
        self.target_plot.setVisible(profile_only)
        if profile_only:
            self.target_plot.clear()
            if profile:
                self.reference_label.setText(
                    f"Saved target: {profile.name} | {profile.source_name} | independent reference scope"
                )
                spectrum = profile.spectrum
                spacing = spectrum.frequency[1] - spectrum.frequency[0]
                db = 10 * np.log10(
                    np.maximum(
                        spectrum.power / max(float(np.sum(spectrum.power)) * spacing, 1e-30), 1e-15
                    )
                )
                self.target_plot.plot(spectrum.frequency[1:], db[1:], pen=pg.mkPen("#eabb6b"))
            else:
                self.reference_label.setText(
                    "Load a reference or import saved targets. The mix stays visible."
                )
        else:
            self.reference_label.setText(
                f"Loaded reference: {loaded[3]} | independent selection/time base"
            )
            for plot in self.reference_waveform.channel_plots:
                plot.setMinimumHeight(65)
        if self.mode.currentData() != "waveform":
            self.timer.start()

    def show_loaded_reference(self):
        library = self.owner.section_workbench.target_list
        library.clearSelection()
        library.setCurrentItem(None)
        self.refresh_reference()

    def request_spectrum(self):
        if self.closing or self.mode.currentData() == "waveform" or self.owner.source is None:
            return
        before, after, rate, title = self.spectral_pair()
        selected = self.waveform.selection
        region = (selected.start, selected.end) if selected else (0, len(before))
        if region[1] - region[0] < max(2, round(rate * 0.1)):
            self.requested_key = None
            self.owner.spectrum_plot.clear()
            self.owner.spectrum_plot.setTitle("Choose at least 0.1 seconds for a stable spectrum")
            return
        target = self.reference_target() if self.owner.views.currentIndex() != 4 else None
        key = (id(before), id(after), rate, region, id(target), title)
        self.requested_key = key
        if key != self.completed_key and key != self.pending_key:
            self.owner.spectrum_plot.clear()
            self.owner.spectrum_plot.setTitle("Updating selected-scope spectrum…")
            self.pending_key = key
        if key == self.completed_key or self.job is not None:
            return
        self.job = SpectrumTask(key, before, after, rate, region)
        self.job.ready.connect(self.spectrum_ready)
        self.job.finished.connect(self.spectrum_finished)
        self.job.start()

    def spectrum_ready(self, payload):
        key, before, after, error = payload
        if self.closing or key != self.requested_key:
            return
        actual_before, actual_after, rate, title = self.spectral_pair()
        selection = self.waveform.selection
        region = (
            (selection.start, selection.end)
            if selection
            else ((0, len(actual_before)) if actual_before is not None else None)
        )
        target = self.reference_target() if self.owner.views.currentIndex() != 4 else None
        if key != (id(actual_before), id(actual_after), rate, region, id(target), title):
            return
        self.owner.spectrum_plot.clear()
        if error:
            self.owner.spectrum_plot.setTitle("Spectrum unavailable: " + error)
        else:
            self.owner.spectrum_plot.setTitle("Before/after tonal balance for the selected scope")
            self.owner.draw_spectrum(
                before,
                "Original" if self.signal.currentData() == "original" else "Step input",
                "#73a8ff",
            )
            if after:
                self.owner.draw_spectrum(after, "Step output", "#63dfc0")
            target = self.reference_target() if self.owner.views.currentIndex() != 4 else None
            if target:
                self.owner.draw_spectrum(target.spectrum, "Reference target", "#eabb6b")
        self.completed_key = key

    def spectral_pair(self):
        before, after, rate, title = self.pairs()
        if self.signal.currentData() == "original" and self.owner.source:
            return self.owner.source[0], None, rate, "Original recording"
        return before, after, rate, title

    def spectrum_finished(self):
        self.job.deleteLater()
        self.job = None
        if self.requested_key != self.completed_key:
            self.timer.start()

    def update_cursor(self):
        rate = (
            self.owner.source[1]
            if self.owner.source
            else (self.owner.project.source.sample_rate if self.owner.project.source else 1)
        )
        self.overview_cursor.setValue(self.owner.position / rate)

    def close_jobs(self):
        self.closing = True
        self.timer.stop()
        if self.job:
            self.job.wait()
