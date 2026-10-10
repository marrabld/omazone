"""The final Export step: review the chain, then write the render to disk."""

from PySide6 import QtCore, QtWidgets

from .workflow_status import AnalysisState, describe

STAGE_TITLES = (
    ("repair", "Repair"),
    ("match", "Match"),
    ("eq", "Manual EQ"),
    ("dynamics", "Dynamics"),
    ("output", "Output gain"),
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
        layout.addWidget(
            QtWidgets.QLabel(
                "Export writes the full-chain render, never a preview. "
                "Review the stages below before writing the file."
            )
        )
        self.chain = QtWidgets.QListWidget()
        self.chain.setMinimumHeight(150)
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
        layout.addStretch(1)
        self.export_button = QtWidgets.QPushButton("Export WAV…")
        self.export_button.clicked.connect(owner.export)
        layout.addWidget(self.export_button)
        self.requested = None
        self.route_target = None

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
        measurements = self.owner.output_view.readouts
        self.peaks.setText(" | ".join(label.text() for label in measurements if label.text()))
        self.summary.setText(
            "Ready to export the rendered chain."
            if workflow.export_available
            else workflow.render_reason
        )
        busy = self.owner.worker is not None
        self.export_button.setEnabled(workflow.export_available and not busy)
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
