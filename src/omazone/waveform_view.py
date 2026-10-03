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
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        help_text = QtWidgets.QLabel(
            "Wheel: zoom | Drag: pan | Shift+drag: select | Drag green edges: adjust | Click: seek"
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        controls = QtWidgets.QHBoxLayout()
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
        layout.addLayout(controls)
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
