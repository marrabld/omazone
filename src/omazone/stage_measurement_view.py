"""One asynchronous before/after readout shared by the processing inspectors."""

import math

from PySide6 import QtCore, QtWidgets

from .stage_measurements import MeasurementCache, request_for


class MeasurementTask(QtCore.QThread):
    def __init__(self, request, cache, known_loudness):
        super().__init__()
        self.request = request
        self.cache = cache
        self.known_loudness = known_loudness
        self.result = None

    def run(self):
        readings, errors = [], []
        for audio in (self.request.before, self.request.after):
            try:
                readings.append(
                    None
                    if audio is None
                    else self.cache.measure(
                        audio,
                        self.request.rate,
                        self.request.start,
                        self.request.end,
                        self.known_loudness,
                    )
                )
            except Exception as error:  # worker failures must become absent readings
                readings.append(None)
                errors.append(str(error))
        self.result = (tuple(readings), tuple(errors))


class StageMeasurementView(QtWidgets.QGroupBox):
    def __init__(self, owner):
        super().__init__("Stage measurements")
        self.owner = owner
        self.cache = MeasurementCache()
        self.request = None
        self.completed_key = None
        self.job = None
        self.closing = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.scope = QtWidgets.QLabel()
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        grid = QtWidgets.QGridLayout()
        grid.addWidget(QtWidgets.QLabel("Before"), 0, 1)
        grid.addWidget(QtWidgets.QLabel("After"), 0, 2)
        self.values = []
        for row, title in enumerate(("Peak (dBFS)", "LUFS (int.)"), 1):
            grid.addWidget(QtWidgets.QLabel(title), row, 0)
            labels = []
            for column in (1, 2):
                label = QtWidgets.QLabel("--")
                side = "Before" if column == 1 else "After"
                label.setAccessibleName(f"{side} {title}")
                label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
                grid.addWidget(label, row, column)
                labels.append(label)
            self.values.append(labels)
        layout.addLayout(grid)
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.note)
        self.setToolTip(
            "Raw stage input/output, before preview loudness matching. Peak is the sample maximum, not true peak. LUFS is gated integrated loudness over the labelled scope; short passages and silence have no LUFS reading."
        )
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.start)
        self.hide()

    def refresh(self):
        if self.closing:
            return
        visible = self.owner.views.currentStep() not in ("listen", "reference", "export")
        self.setVisible(visible)
        if not visible:
            self.request = None
            self.timer.stop()
            return
        key = self.owner.workspace.viewer_context_key()
        state = self.owner.comparison(key)
        request = request_for(
            state, self.owner.workflow_status().stages, self.owner.waveform.selection
        )
        if self.request is not None and request.key == self.request.key:
            return
        self.request = request
        self.completed_key = None
        self.timer.stop()
        self.scope.setText(f"{request.title} | {request.scope}")
        self.note.setText(request.status)
        for row in self.values:
            for label in row:
                label.setText("--")
                label.setToolTip("")
        if request.before is not None or request.after is not None:
            self.note.setText(request.status + " Measuring...")
            self.timer.start()

    def start(self):
        if self.closing or self.job is not None or self.request is None:
            return
        if self.request.before is None and self.request.after is None:
            return
        if self.completed_key == self.request.key:
            return
        if self.owner.worker is not None:
            self.timer.start()
            return
        chain = self.owner.chain_result
        known = dict(chain.loudness) if chain is not None else {}
        self.job = MeasurementTask(self.request, self.cache, known)
        self.job.finished.connect(self.finished)
        self.job.start()

    def finished(self):
        job, self.job = self.job, None
        if not self.closing and self.request is not None:
            if job.request.key == self.request.key:
                self.completed_key = self.request.key
                readings, errors = job.result
                for column, reading in enumerate(readings):
                    if reading is None:
                        continue
                    self.values[0][column].setText(
                        "-inf" if math.isinf(reading.peak_db) else f"{reading.peak_db:+.1f}"
                    )
                    self.values[1][column].setText(
                        "--" if reading.lufs is None else f"{reading.lufs:+.1f}"
                    )
                    self.values[1][column].setToolTip(reading.loudness_note)
                extra = []
                for title, reading in zip(("Before", "After"), readings, strict=True):
                    if reading is not None and reading.loudness_note:
                        extra.append(f"{title}: {reading.loudness_note}.")
                if errors:
                    extra.append("Measurement unavailable: " + "; ".join(errors))
                self.note.setText(" ".join((self.request.status, *extra)))
            else:
                self.timer.start()
        job.deleteLater()

    def close_jobs(self):
        self.closing = True
        self.timer.stop()
        self.request = None
        if self.job is not None:
            self.job.wait()
