"""The final Export step: review the chain, then write the render to disk."""

import weakref

import numpy as np
from PySide6 import QtCore, QtWidgets

from .delivery import (
    FORMATS,
    DeliverySettings,
    format_label,
    measure_peak,
    quantises,
    suggested_attenuation,
)
from .workflow_status import AnalysisState, describe

STAGE_TITLES = (
    ("repair", "Repair"),
    ("match", "Match"),
    ("eq", "Manual EQ"),
    ("filters", "Corrective filters"),
    ("dynamics", "Dynamics"),
    ("output", "Output gain"),
    ("limiter", "Final limiter"),
)


class ExportView(QtWidgets.QWidget):
    """A chain summary plus the action that writes the rendered audio.

    Export is the last step rather than a toolbar button so the learner can see
    what is about to be written, which stages are current, and whether matching
    analysis is fresh.
    """

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        prompt = QtWidgets.QLabel(
            "Exports the full rendered chain, never a preview. Review the stages, "
            "choose a format, then click Export WAV. This is the final step."
        )
        prompt.setWordWrap(True)
        prompt.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(prompt)
        self.chain = QtWidgets.QListWidget()
        self.chain.setMinimumHeight(122)
        self.chain.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chain.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.chain)
        self.analysis = QtWidgets.QLabel()
        self.analysis.setWordWrap(True)
        layout.addWidget(self.analysis)
        self.peaks = QtWidgets.QLabel()
        self.peaks.setWordWrap(True)
        layout.addWidget(self.peaks)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.route = QtWidgets.QPushButton()
        self.route.clicked.connect(self.activate_route)
        self.route.setVisible(False)
        layout.addWidget(self.route)
        self.delivery = QtWidgets.QGroupBox("Delivery format")
        delivery_layout = QtWidgets.QVBoxLayout(self.delivery)
        # The panel wraps prose like the other pages, so it must not force the
        # window wider than the narrow-layout budget.
        self.delivery.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        format_row = QtWidgets.QHBoxLayout()
        format_label_widget = QtWidgets.QLabel("Write as")
        format_label_widget.setProperty("formLabel", True)
        format_row.addWidget(format_label_widget)
        self.format = QtWidgets.QComboBox()
        for key, title, _ in FORMATS:
            self.format.addItem(title, key)
        format_row.addWidget(self.format)
        format_row.addStretch(1)
        delivery_layout.addLayout(format_row)
        self.dither = QtWidgets.QCheckBox("Apply dither")
        self.dither.setChecked(True)
        self.dither.setToolTip(
            "Adds standard TPDF noise before 24-bit quantisation. Dither once, last. "
            "If more processing is planned, export float instead."
        )
        delivery_layout.addWidget(self.dither)
        self.delivery_note = QtWidgets.QLabel()
        self.delivery_note.setWordWrap(True)
        delivery_layout.addWidget(self.delivery_note)
        for label in self.delivery.findChildren(QtWidgets.QLabel):
            if label.property("formLabel"):
                continue
            label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
            )
        layout.addWidget(self.delivery)
        layout.addStretch(1)
        self.export_button = QtWidgets.QPushButton("Export WAV…")
        self.export_button.clicked.connect(owner.export)
        self.requested = None
        self.route_target = None
        self._peak_source = None
        self._peak = None
        self.format.currentIndexChanged.connect(lambda: self.refresh_delivery())
        self.dither.toggled.connect(lambda: self.refresh_delivery())
        self.refresh_delivery()

    def delivery_settings(self):
        """The chosen target format. Applies at write time only, never to playback."""
        return DeliverySettings(
            format=self.format.currentData(), dither=self.dither.isChecked()
        )

    def reset_delivery(self):
        with QtCore.QSignalBlocker(self.format), QtCore.QSignalBlocker(self.dither):
            self.format.setCurrentIndex(0)
            self.dither.setChecked(True)
        self._peak_source = None
        self._peak = None
        self.refresh_delivery()

    def refresh_delivery(self):
        settings = self.delivery_settings()
        self.dither.setEnabled(quantises(settings))
        report = self.output_view_delivery_report()
        if report is None:
            self.delivery_note.setText("Render the chain to see this format's headroom.")
        elif report.clipped:
            self.delivery_note.setText(
                f"Peak {report.peak_dbfs:+.2f} dBFS exceeds this format's ceiling"
                + (", including dither headroom. " if report.dithered else ". ")
                + f"Export will ask to attenuate by {suggested_attenuation(report):.2f} dB."
            )
        elif not quantises(settings):
            self.delivery_note.setText(
                ("Silence. " if report.peak_dbfs == float("-inf") else
                 f"Peak {report.peak_dbfs:+.2f} dBFS. ")
                + "Float preserves over-0 dBFS samples, but playback can still clip. No dither is used."
            )
        else:
            self.delivery_note.setText(
                ("Silence fits this format" if report.peak_dbfs == float("-inf") else
                 f"Peak {report.peak_dbfs:+.2f} dBFS fits this format")
                + (" with dither applied." if report.dithered else " without dither.")
            )
        # The action row is shared by every step and has a fixed narrow-width
        # budget, so the button names the step, not the format. The selected
        # format is stated in the panel and in the tooltip.
        self.export_button.setText("Export WAV…")
        self.export_button.setToolTip(
            f"Choose a filename and save the full rendered chain as {format_label(settings)}. "
            "Preview loudness matching is not included."
        )

    def output_view_delivery_report(self):
        if self.owner.chain_result is None:
            self._peak_source = None
            self._peak = None
            return None
        audio = self.owner.chain_result.output
        if self._peak_source is None or self._peak_source() is not audio:
            view = self.owner.output_view
            self._peak = (
                view.final_peak if view.measured_result is self.owner.chain_result else
                float(np.max(np.abs(audio)))
            )
            self._peak_source = weakref.ref(audio)
        return measure_peak(self._peak, self.delivery_settings())

    def rows(self, workflow):
        """One row per processing stage, in the order the chain renders them."""
        return [
            (workflow.stages[stage].state, f"{title}: {describe(workflow.stages[stage].state)}")
            for stage, title in STAGE_TITLES
        ]

    def draw(self, workflow):
        """Refresh the summary from the shared status, not from local flags."""
        wanted = [(state.value, text) for state, text in self.rows(workflow)]
        if wanted != self.requested:
            self.requested = wanted
            self.chain.clear()
            for state, text in wanted:
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, state)
                self.chain.addItem(item)
        analysis = workflow.matching_analysis
        if analysis is AnalysisState.CURRENT:
            self.analysis.setText("Matching analysis: learned from this recording.")
        elif analysis is AnalysisState.RETAINED:
            self.analysis.setText(
                "Matching analysis is retained from an earlier input. It is valid to render with, "
                "and flagged so you know it was not learned from this recording."
            )
        else:
            self.analysis.setText(
                "Matching analysis is missing. Analyse or skip Matching before rendering."
            )
        # Keep LUFS and true-peak warnings visible. A safe sample peak alone
        # does not guarantee safe reconstruction or a streaming-ready file.
        final = self.owner.output_view.readouts[-1].text() if self.owner.output_view.readouts else ""
        self.peaks.setText(
            final.replace("Final export:", "Render before delivery:", 1)
            or "Render to measure the final export peak."
        )
        self.summary.setText(
            "Ready to export the rendered chain."
            if workflow.export_available
            else workflow.render_reason
        )
        busy = self.owner.worker is not None
        self.delivery.setEnabled(not busy)
        self.export_button.setEnabled(workflow.export_available and not busy)
        self.refresh_delivery()
        self.set_route(workflow, busy)

    def set_route(self, workflow, busy=False):
        """Offer a way to whatever is holding export up.

        Naming the problem is only half of it; the learner should not have to
        hunt through the navigation for the step that needs attention. With no
        recording there is no step to visit, so the route opens the loader.
        """
        stage = workflow.blocking_stage
        step = self.owner.step_for_stage(stage)
        if workflow.export_available:
            self.hide_route()
            return
        if step is not None:
            self.route_target = ("step", step.key)
            self.route.setText(f"Go to {step.title}")
        elif workflow.render_action in ("Load", "Relink"):
            self.route_target = ("load", workflow.render_action)
            self.route.setText(
                "Relink the recording…" if workflow.render_action == "Relink" else "Load a mix…"
            )
        else:
            self.hide_route()
            return
        self.route.setToolTip(workflow.render_reason)
        self.route.setEnabled(not busy)
        self.route.setVisible(True)

    def hide_route(self):
        self.route_target = None
        self.route.setVisible(False)
        self.route.setText("")
        self.route.setToolTip("")

    def activate_route(self):
        if self.route_target is None:
            return
        kind, value = self.route_target
        if kind == "step":
            self.owner.views.setStep(value)
        else:
            self.owner.load("source")
