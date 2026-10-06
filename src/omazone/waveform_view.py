"""Linked waveform plots with draggable, sample-snapped selection."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .waveform import SampleRegion


class SelectionViewBox(pg.ViewBox):
    selected = QtCore.Signal(float, float)

    def mouseDragEvent(self, event, axis=None):
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
        ):
            event.accept()
            start = self.mapSceneToView(event.buttonDownScenePos()).x()
            end = self.mapSceneToView(event.scenePos()).x()
            self.selected.emit(start, end)
        else:
            super().mouseDragEvent(event, axis)


class WaveformView(QtWidgets.QWidget):
    seek_requested = QtCore.Signal(int)
    selection_changed = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.index = None
        self.sample_rate = 1
        self.selection = None
        self.syncing = False
        self.channel_plots = []
        self.curves = []
        self.regions = []
        self.playheads = []
        self.section_items = []
        self.clipping_items = []
        self.clipping_markers = []
        self.repair_items = []
        self.repair_data = []
        self.eq_items = []
        self.eq_scope_key = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.help_text = QtWidgets.QLabel(
            "Wheel: zoom | Drag: pan | Shift+drag: select | Drag green edges: adjust | Click: seek"
        )
        self.help_text.setWordWrap(True)
        layout.addWidget(self.help_text)
        self.selection_controls = QtWidgets.QWidget()
        controls = QtWidgets.QHBoxLayout(self.selection_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.start_time = self.time_control(controls, "Start")
        self.end_time = self.time_control(controls, "End")
        self.start_time.valueChanged.connect(self.edit_times)
        self.end_time.valueChanged.connect(self.edit_times)
        self.buttons = []
        for title, action in (
            ("Select view", self.select_view),
            ("Zoom selection", self.zoom_selection),
            ("Fit song", self.fit_song),
            ("Seek start", self.seek_start),
            ("Clear", lambda: self.set_selection(None)),
        ):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(action)
            controls.addWidget(button)
            self.buttons.append(button)
        layout.addWidget(self.selection_controls)
        self.selection_label = QtWidgets.QLabel("No region selected.")
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)
        self.plot_layout = QtWidgets.QVBoxLayout()
        layout.addLayout(self.plot_layout, 1)
        self.redraw_timer = QtCore.QTimer(self)
        self.redraw_timer.setSingleShot(True)
        self.redraw_timer.setInterval(30)
        self.redraw_timer.timeout.connect(self.redraw)
        self.setEnabled(False)

    def time_control(self, layout, label):
        layout.addWidget(QtWidgets.QLabel(label))
        control = QtWidgets.QDoubleSpinBox()
        control.setDecimals(9)
        control.setSuffix(" s")
        layout.addWidget(control)
        return control

    def set_audio(self, index, sample_rate):
        self.redraw_timer.stop()
        for plot in self.channel_plots:
            plot.hide()
            self.plot_layout.removeWidget(plot)
            plot.deleteLater()
        self.channel_plots, self.curves, self.regions, self.playheads = [], [], [], []
        self.section_items = []
        self.clipping_items = []
        self.clipping_markers = []
        self.repair_items = []
        self.repair_data = []
        self.eq_items = []
        self.eq_scope_key = None
        self.index = index
        self.sample_rate = sample_rate
        duration = len(index.audio) / sample_rate
        for control in (self.start_time, self.end_time):
            with QtCore.QSignalBlocker(control):
                control.setRange(0, duration)
                control.setSingleStep(1 / sample_rate)
                control.setValue(0)
        for channel in range(index.audio.shape[1]):
            view = SelectionViewBox()
            view.setMouseEnabled(x=True, y=False)
            view.setLimits(xMin=0, xMax=duration, minXRange=2 / sample_rate)
            view.selected.connect(self.select_seconds)
            plot = pg.PlotWidget(viewBox=view)
            plot.setLabel("bottom", "Time", units="s")
            label = "Mono" if index.audio.shape[1] == 1 else ("Left", "Right")[channel]
            plot.setLabel("left", label)
            plot.showGrid(x=True, y=True, alpha=0.15)
            peak = max(1.0, float(index.peak[channel]))
            plot.setYRange(-peak * 1.05, peak * 1.05, padding=0)
            plot.setMinimumHeight(110)
            curve = plot.plot(pen=pg.mkPen("#73a8ff" if channel == 0 else "#eabb6b", width=1))
            region = pg.LinearRegionItem(
                values=(0, 0),
                bounds=(0, duration),
                brush=pg.mkBrush(99, 223, 192, 35),
                pen=pg.mkPen("#63dfc0"),
                swapMode="sort",
            )
            region.setZValue(10)
            plot.addItem(region)
            region.hide()
            region.sigRegionChanged.connect(self.region_dragged)
            region.sigRegionChangeFinished.connect(self.snap_regions)
            playhead = pg.InfiniteLine(pos=0, pen=pg.mkPen("#ffffff", width=1))
            playhead.setZValue(20)
            plot.addItem(playhead)
            plot.scene().sigMouseClicked.connect(self.clicked)
            view.sigXRangeChanged.connect(self.schedule_redraw)
            if self.channel_plots:
                plot.setXLink(self.channel_plots[0])
            self.channel_plots.append(plot)
            self.curves.append(curve)
            self.regions.append(region)
            self.playheads.append(playhead)
            self.plot_layout.addWidget(plot, 1)
        self.setEnabled(True)
        self.set_selection(None)
        self.fit_song()
        self.redraw()

    def clear_audio(self):
        self.redraw_timer.stop()
        for plot in self.channel_plots:
            plot.hide()
            self.plot_layout.removeWidget(plot)
            plot.deleteLater()
        self.channel_plots, self.curves, self.regions, self.playheads = [], [], [], []
        self.section_items, self.clipping_items, self.clipping_markers = [], [], []
        self.repair_items, self.repair_data = [], []
        self.eq_items, self.eq_scope_key = [], None
        self.index = None
        self.set_selection(None)
        self.setEnabled(False)

    def schedule_redraw(self, *args):
        self.redraw_timer.start()

    def redraw(self):
        if self.index is None:
            return
        left, right = self.channel_plots[0].viewRange()[0]
        budget = max(100, min(2000, self.channel_plots[0].width()))
        samples, low, high = self.index.visible(
            int(np.floor(left * self.sample_rate)), int(np.ceil(right * self.sample_rate)), budget
        )
        times = samples / self.sample_rate
        raw = low is high
        for channel, curve in enumerate(self.curves):
            if raw:
                curve.setData(times, low[:, channel], connect="all")
            else:
                # Draw vertical min/max pairs, never a decimated line that loses peaks.
                curve.setData(
                    np.repeat(times, 2),
                    np.column_stack((low[:, channel], high[:, channel])).ravel(),
                    connect="pairs",
                )
        self.redraw_clipping(left, right, budget)
        self.redraw_repair(left, right, budget)

    def select_seconds(self, start, end):
        if self.index is not None:
            self.set_selection(
                SampleRegion.from_seconds(start, end, self.sample_rate, len(self.index.audio))
            )

    def set_selection(self, region, update_regions=True):
        self.selection = region if region is not None and region.end > region.start else None
        self.syncing = True
        try:
            start = self.selection.start if self.selection else 0
            end = self.selection.end if self.selection else 0
            for control, value in ((self.start_time, start), (self.end_time, end)):
                with QtCore.QSignalBlocker(control):
                    control.setValue(value / self.sample_rate)
            if update_regions:
                for item in self.regions:
                    item.setRegion((start / self.sample_rate, end / self.sample_rate))
                    item.setVisible(self.selection is not None)
            if self.selection is None:
                self.selection_label.setText("No region selected. Bounds use [start, end) samples.")
            else:
                self.selection_label.setText(
                    f"Samples [{start}, {end}) | {end - start} samples | "
                    f"Duration {(end - start) / self.sample_rate:.6f} s"
                )
        finally:
            self.syncing = False
        self.selection_changed.emit(self.selection)

    def region_dragged(self, item):
        if self.syncing or self.index is None:
            return
        region = SampleRegion.from_seconds(
            *item.getRegion(), self.sample_rate, len(self.index.audio)
        )
        self.set_selection(region, update_regions=False)
        self.syncing = True
        try:
            for other in self.regions:
                if other is not item:
                    other.setRegion(item.getRegion())
                    other.setVisible(self.selection is not None)
        finally:
            self.syncing = False

    def snap_regions(self, *args):
        if not self.syncing:
            self.set_selection(self.selection)

    def edit_times(self):
        self.select_seconds(self.start_time.value(), self.end_time.value())

    def select_view(self):
        if self.channel_plots:
            self.select_seconds(*self.channel_plots[0].viewRange()[0])

    def zoom_selection(self):
        if self.selection is not None:
            self.channel_plots[0].setXRange(
                self.selection.start / self.sample_rate,
                self.selection.end / self.sample_rate,
                padding=0,
            )

    def fit_song(self):
        if self.index is not None:
            self.channel_plots[0].setXRange(0, len(self.index.audio) / self.sample_rate, padding=0)

    def seek_start(self):
        if self.selection is not None:
            self.seek_requested.emit(self.selection.start)

    def clicked(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton or event.isAccepted():
            return
        for plot in self.channel_plots:
            if plot.getViewBox().sceneBoundingRect().contains(event.scenePos()):
                time = plot.getViewBox().mapSceneToView(event.scenePos()).x()
                self.seek_requested.emit(
                    max(0, min(len(self.index.audio), round(time * self.sample_rate)))
                )
                event.accept()
                break

    def set_position(self, samples):
        for line in self.playheads:
            line.setValue(samples / self.sample_rate)

    def set_eq_scope(self, bounds, transition_ms=0, gain_db=0, frequency=0):
        key = (
            bounds,
            transition_ms,
            gain_db,
            frequency,
            tuple(id(plot) for plot in self.channel_plots),
        )
        if key == self.eq_scope_key:
            return
        for plot, item in self.eq_items:
            plot.removeItem(item)
        self.eq_items = []
        self.eq_scope_key = key
        if bounds is None:
            return
        fade = min(round(transition_ms * self.sample_rate / 1000), (bounds.end - bounds.start) // 2)
        windows = []
        if bounds.start > 0 and fade:
            windows.append((bounds.start, bounds.start + fade))
        if self.index is not None and bounds.end < len(self.index.audio) and fade:
            windows.append((bounds.end - fade, bounds.end))
        for plot in self.channel_plots:
            for start, end in windows:
                window = pg.LinearRegionItem(
                    values=(start / self.sample_rate, end / self.sample_rate),
                    movable=False,
                    brush=pg.mkBrush(201, 155, 255, 35),
                    pen=pg.mkPen("#c99bff", style=QtCore.Qt.PenStyle.DashLine),
                )
                window.setZValue(-3)
                window.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                for line in window.lines:
                    line.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                plot.addItem(window, ignoreBounds=True)
                self.eq_items.append((plot, window))

    def set_sections(self, sections, transitions, colors):
        for plot, item in self.section_items:
            plot.removeItem(item)
        self.section_items = []
        for channel, plot in enumerate(self.channel_plots):
            for index, section in enumerate(sections):
                bounds = section.region if hasattr(section, "region") else section.bounds
                color = colors[index % len(colors)]
                shade = pg.mkColor(color)
                shade.setAlpha(24)
                region = pg.LinearRegionItem(
                    values=(
                        bounds.start / self.sample_rate,
                        bounds.end / self.sample_rate,
                    ),
                    movable=False,
                    brush=pg.mkBrush(shade),
                    pen=pg.mkPen(color),
                )
                region.setZValue(-5)
                region.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                for line in region.lines:
                    line.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                plot.addItem(region, ignoreBounds=True)
                label = pg.TextItem(section.name, color=color, anchor=(0.5, 0))
                label.setPos(
                    (bounds.start + bounds.end) / (2 * self.sample_rate),
                    max(1.0, float(self.index.peak[channel])),
                )
                label.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                plot.addItem(label, ignoreBounds=True)
                self.section_items.extend(((plot, region), (plot, label)))
            for transition in transitions:
                region = pg.LinearRegionItem(
                    values=(transition.start / self.sample_rate, transition.end / self.sample_rate),
                    movable=False,
                    brush=pg.mkBrush(234, 187, 107, 24),
                    pen=pg.mkPen("#eabb6b", style=QtCore.Qt.PenStyle.DashLine),
                )
                region.setZValue(-4)
                region.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                for line in region.lines:
                    line.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                plot.addItem(region, ignoreBounds=True)
                self.section_items.append((plot, region))

    def set_clipping(self, report):
        for plot, item in self.clipping_items:
            plot.removeItem(item)
        self.clipping_items = []
        self.clipping_markers = []
        if report is None:
            return
        for stats in report.stats:
            plot = self.channel_plots[stats.channel]
            for intervals, color in ((report.candidates, "#ff6b6b"), (report.overloads, "#ffb45c")):
                channel_intervals = sorted(
                    (item for item in intervals if item.channel == stats.channel),
                    key=lambda item: item.start,
                )
                if not channel_intervals:
                    continue
                centers = np.asarray(
                    [
                        (item.start + item.end - 1) / (2 * self.sample_rate)
                        for item in channel_intervals
                    ]
                )
                levels = np.asarray([item.level for item in channel_intervals])
                marker = pg.ScatterPlotItem(
                    size=7, pen=pg.mkPen(color), brush=pg.mkBrush(color), symbol="o"
                )
                marker.setZValue(15)
                marker.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                plot.addItem(marker, ignoreBounds=True)
                self.clipping_items.append((plot, marker))
                self.clipping_markers.append((marker, centers, levels))
            settings = report.settings_for(stats.channel)
            for threshold in (settings.positive, settings.negative):
                line = pg.InfiniteLine(
                    pos=threshold,
                    angle=0,
                    pen=pg.mkPen("#ff6b6b", style=QtCore.Qt.PenStyle.DashLine),
                )
                line.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                plot.addItem(line, ignoreBounds=True)
                self.clipping_items.append((plot, line))
        left, right = self.channel_plots[0].viewRange()[0]
        self.redraw_clipping(left, right, max(100, min(2000, self.channel_plots[0].width())))

    def redraw_clipping(self, left, right, budget):
        for marker, times, levels in self.clipping_markers:
            first, last = np.searchsorted(times, (left, right))
            # Bound GUI work on dense clipped passages. The inspector totals
            # include every interval; zooming reveals the individual markers.
            stride = max(1, int(np.ceil((last - first) / budget)))
            marker.setData(times[first:last:stride], levels[first:last:stride])

    def set_repair(self, result):
        for plot, curve in self.repair_items:
            plot.removeItem(curve)
        self.repair_items = []
        self.repair_data = []
        for channel, plot in enumerate(self.channel_plots):
            peak = max(1.0, float(self.index.peak[channel]))
            if result is not None:
                intervals = sorted(
                    (item for item in result.repaired if item.channel == channel),
                    key=lambda item: item.start,
                )
                if intervals:
                    peak = max(
                        peak,
                        max(
                            float(np.max(np.abs(result.audio[item.start : item.end, channel])))
                            for item in intervals
                        ),
                    )
                    curve = plot.plot(pen=pg.mkPen("#63dfc0", width=2), connect="finite")
                    curve.setZValue(12)
                    self.repair_items.append((plot, curve))
                    starts = np.asarray([item.start for item in intervals])
                    ends = np.asarray([item.end for item in intervals])
                    self.repair_data.append((curve, channel, starts, ends, result.audio))
            plot.setYRange(-peak * 1.05, peak * 1.05, padding=0)
        if self.channel_plots:
            left, right = self.channel_plots[0].viewRange()[0]
            self.redraw_repair(left, right, max(100, min(2000, self.channel_plots[0].width())))

    def redraw_repair(self, left, right, budget):
        sample_left, sample_right = left * self.sample_rate, right * self.sample_rate
        for curve, channel, starts, ends, audio in self.repair_data:
            first = np.searchsorted(ends, sample_left, side="right")
            last = np.searchsorted(starts, sample_right)
            interval_stride = max(1, int(np.ceil((last - first) / max(1, budget // 3))))
            chosen = list(range(first, last, interval_stride))
            per_interval = max(2, budget // max(1, len(chosen)))
            times, values = [], []
            for index in chosen:
                start = max(int(starts[index]), int(np.floor(sample_left)))
                end = min(int(ends[index]), int(np.ceil(sample_right)))
                if end <= start:
                    continue
                samples = np.unique(
                    np.linspace(start, end - 1, min(end - start, per_interval), dtype=int)
                )
                times.append(np.concatenate((samples / self.sample_rate, [np.nan])))
                values.append(np.concatenate((audio[samples, channel], [np.nan])))
            curve.setData(
                np.concatenate(times) if times else [],
                np.concatenate(values) if values else [],
                connect="finite",
            )
