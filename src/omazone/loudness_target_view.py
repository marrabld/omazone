"""Inspectable loudness proposals; only Apply changes existing output stages."""

import weakref
from dataclasses import asdict, replace

from PySide6 import QtCore, QtWidgets

from .delivery import dbfs
from .delivery_measurements import DeliveryMeasurements
from .limiter import limiter_parameters
from .limiter import settings_from_parameters as limiter_settings
from .limiter import validate_settings as validate_limiter
from .loudness import format_lufs
from .loudness_target import (
    LOUDNESS_TOLERANCE,
    PEAK_TOLERANCE,
    TargetSettings,
    propose_target,
    settings_from_parameters,
    target_parameters,
)
from .output_gain import OutputGainSettings, output_parameters
from .output_gain import settings_from_parameters as gain_settings
from .output_gain import validate_settings as validate_gain
from .project import digest_json
from .truepeak import format_true_peak


class TargetTask(QtCore.QThread):
    progress = QtCore.Signal(str)

    def __init__(self, audio, rate, settings, delivery, limiter, key, baseline):
        super().__init__()
        self.audio, self.rate = audio, rate
        self.settings, self.delivery, self.limiter = settings, delivery, limiter
        self.key, self.baseline = key, baseline
        self.result, self.error = None, None
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True
        self.requestInterruption()

    def cancelled(self):
        return self.cancel_requested or self.isInterruptionRequested()

    def run(self):
        try:
            self.result = propose_target(
                self.audio,
                self.rate,
                self.settings,
                self.delivery,
                self.limiter,
                self.baseline,
                self.cancelled,
                self.progress.emit,
            )
        except Exception as error:
            self.error = str(error)


class LoudnessTargetView(QtWidgets.QGroupBox):
    def __init__(self, owner):
        super().__init__("Loudness target")
        self.owner = owner
        self.job = None
        self.proposal = None
        self.proposal_key = None
        self.proposal_audio = None
        self.closing = False
        self.restoring = False
        self.original = TargetSettings()
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "Whole recording, at the delivery rate selected in Export. Choose your own/client's target; "
            "there is no universal streaming target. Calculating does not change audio."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(QtWidgets.QLabel("Requested integrated loudness"))
        self.lufs = QtWidgets.QDoubleSpinBox()
        self.lufs.setRange(-60, 0)
        self.lufs.setDecimals(1)
        self.lufs.setSuffix(" LUFS")
        layout.addWidget(self.lufs)
        layout.addWidget(QtWidgets.QLabel("Requested delivery peak ceiling"))
        self.ceiling = QtWidgets.QDoubleSpinBox()
        self.ceiling.setRange(-24, 0)
        self.ceiling.setDecimals(1)
        self.ceiling.setSuffix(" dBTP")
        layout.addWidget(self.ceiling)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Gain only, limiter off", "gain-only")
        self.mode.addItem("Enable final limiter", "limiter")
        layout.addWidget(self.mode)
        self.calculate_button = QtWidgets.QPushButton("Calculate proposal")
        self.calculate_button.clicked.connect(self.calculate)
        layout.addWidget(self.calculate_button)
        self.cancel_button = QtWidgets.QPushButton("Cancel calculation")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        layout.addWidget(self.cancel_button)
        self.message = QtWidgets.QLabel(
            "Render the chain, then calculate a proposal. Apply is a separate decision."
        )
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.apply_button = QtWidgets.QPushButton("Apply proposal and render")
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setEnabled(False)
        layout.addWidget(self.apply_button)
        self.actual = QtWidgets.QLabel()
        self.actual.setWordWrap(True)
        layout.addWidget(self.actual)
        self.written = QtWidgets.QLabel()
        self.written.setWordWrap(True)
        layout.addWidget(self.written)
        for label in self.findChildren(QtWidgets.QLabel):
            label.setWordWrap(True)
            label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
            )
        self.restore()
        self.lufs.valueChanged.connect(self.request_changed)
        self.ceiling.valueChanged.connect(self.request_changed)
        self.mode.currentIndexChanged.connect(self.request_changed)
        self.hide()

    def settings(self):
        return TargetSettings(
            self.owner.retained_number(self.lufs, self.original.target_lufs),
            self.owner.retained_number(self.ceiling, self.original.ceiling_db),
            self.mode.currentData(),
        )

    def preferences(self):
        return target_parameters(self.settings())

    def restore(self):
        self.restoring = True
        try:
            self.original = settings_from_parameters(self.owner.project.view.get("loudness_target"))
            self.lufs.setValue(self.original.target_lufs)
            self.ceiling.setValue(self.original.ceiling_db)
            self.mode.setCurrentIndex(self.mode.findData(self.original.mode))
        finally:
            self.restoring = False
        self.clear_proposal()
        self.message.setText(
            "Render the chain, then calculate a proposal. Apply is a separate decision."
        )

    def clear_proposal(self):
        if self.job is not None:
            self.job.cancel()
        self.proposal = self.proposal_key = self.proposal_audio = None
        self.apply_button.setEnabled(False)

    def request_changed(self, *_):
        if self.restoring or self.owner.restoring_project:
            return
        # Retain loaded precision until this field is explicitly edited.
        if self.sender() is self.lufs:
            self.original = replace(self.original, target_lufs=self.lufs.value())
        elif self.sender() is self.ceiling:
            self.original = replace(self.original, ceiling_db=self.ceiling.value())
        self.clear_proposal()
        self.owner.project_changed(processing=False)
        self.message.setText(
            "Request changed. Calculate a new proposal; no output settings have changed."
        )
        self.refresh()

    def context(self):
        if (
            self.closing
            or not hasattr(self.owner, "export_view")
            or self.owner.worker is not None
            or self.owner.source is None
            or self.owner.output is None
            or self.owner.chain_result is None
            or not self.owner.workflow_status().export_available
        ):
            return None
        audio = self.owner.chain_result.pre_output
        if audio is None:
            return None
        key = (
            id(audio),
            self.owner.source[1],
            digest_json(
                {
                    "target": self.preferences(),
                    "delivery": asdict(self.owner.export_view.delivery_settings()),
                    "output": asdict(self.owner.project.stages["output"]),
                    "limiter": asdict(self.owner.project.stages["limiter"]),
                }
            ),
        )
        return audio, self.owner.source[1], key

    def calculate(self):
        context = self.context()
        if context is None or self.job is not None:
            self.message.setText("Render the current full chain first.")
            return
        audio, rate, key = context
        try:
            validate_gain(gain_settings(self.owner.project.stages["output"].parameters))
            limiting = limiter_settings(self.owner.project.stages["limiter"].parameters)
            validate_limiter(limiting)
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.clear_proposal()
        delivery = self.owner.export_view.delivery_settings()
        baseline = None
        if (delivery.sample_rate or rate) == rate:
            chain = self.owner.chain_result
            peak = self.owner.output_view.before_peak
            if peak is not None:
                baseline = DeliveryMeasurements(
                    rate,
                    len(audio),
                    peak,
                    chain.true_peak.get(id(audio)),
                    chain.loudness.get(id(audio)),
                )
        self.job = TargetTask(audio, rate, self.settings(), delivery, limiting, key, baseline)
        self.job.progress.connect(self.progressed)
        self.job.finished.connect(self.finished)
        self.message.setText(
            "Calculating a proposal. Existing processing and audition are unchanged…"
        )
        self.job.start()
        self.refresh()

    def progressed(self, text):
        if (
            not self.closing
            and self.sender() is self.job
            and self.context() is not None
            and not self.job.cancel_requested
        ):
            self.message.setText(text)

    def cancel(self):
        self.clear_proposal()
        self.message.setText("Cancellation requested. No output choices have changed.")
        self.refresh()

    def finished(self):
        job, self.job = self.job, None
        context = self.context()
        if not self.closing:
            if (
                context is None
                or context[0] is not job.audio
                or context[2] != job.key
                or job.cancel_requested
            ):
                self.message.setText(
                    "Calculation cancelled or choices changed. Calculate a new proposal when ready."
                )
            elif job.error:
                self.message.setText("No proposal: " + job.error)
            else:
                self.proposal = proposal = job.result
                self.proposal_key = job.key
                self.proposal_audio = weakref.ref(job.audio)
                limiter = (
                    f"Enable final limiter at {proposal.limiter.ceiling_db:+.1f} dBTP; "
                    f"maximum evaluated reduction {proposal.reduction_db:.1f} dB."
                    if proposal.limiter
                    else "Final limiter OFF; stored limiter choices are retained."
                )
                self.message.setText(
                    f"Proposed output gain {proposal.gain_db:+.1f} dB. {limiter} "
                    f"Evaluated float delivery at {proposal.measured.rate / 1000:g} kHz: "
                    f"{format_lufs(proposal.measured.lufs)}; {format_true_peak(proposal.measured.true_peak_db)}. "
                    f"{proposal.evaluations} candidate evaluations. {proposal.reason} "
                    "More limiting can trade dynamics for level. Compare by listening."
                )
            self.refresh()
        job.deleteLater()

    def confirm_unmet(self, proposal):
        return (
            QtWidgets.QMessageBox.question(
                self,
                "Loudness target not reached",
                f"The best evaluated safe result is {proposal.measured.lufs:+.2f} LUFS, not "
                f"the requested {proposal.request.target_lufs:+.1f} LUFS. {proposal.reason} "
                "Apply these shown settings and render anyway?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            == QtWidgets.QMessageBox.StandardButton.Yes
        )

    def apply(self):
        context = self.context()
        proposal = self.proposal
        if (
            proposal is None
            or self.job is not None
            or context is None
            or context[2] != self.proposal_key
            or self.proposal_audio() is not context[0]
        ):
            self.message.setText("Proposal is stale. Calculate a new one before applying.")
            return
        if not proposal.converged and not self.confirm_unmet(proposal):
            return
        # Only this explicit action commits to the existing stages. No new gain
        # stage, export attenuation, or matching analysis is introduced.
        output = self.owner.project.stages["output"]
        output.parameters = output_parameters(OutputGainSettings(proposal.gain_db))
        output.bypassed = False
        limiter = self.owner.project.stages["limiter"]
        limiter.bypassed = proposal.limiter is None
        if proposal.limiter is not None:
            limiter.parameters = limiter_parameters(proposal.limiter)
        self.owner.invalidate_output()
        self.owner.output_view.restore()
        self.message.setText(
            "Proposed settings applied to output gain/limiter. The measured delivery below determines whether the request is met."
        )
        self.owner.output_view.comparison_selector.setCurrentIndex(
            0 if proposal.limiter is None else 1
        )
        self.owner.render_saved_recipe()

    @staticmethod
    def outcome(lufs, true_peak, settings, sample_peak=0):
        if lufs is None or true_peak is None:
            return "Target cannot be assessed: loudness/true peak is unavailable for silence, gated or short material."
        if (
            abs(lufs - settings.target_lufs) <= LOUDNESS_TOLERANCE
            and max(true_peak, dbfs(sample_peak)) <= settings.ceiling_db + PEAK_TOLERANCE
        ):
            return "Measured numbers meet the request within 0.1 LU / 0.01 dB tolerances, not a sound-quality judgement."
        return f"Request not met: loudness difference {lufs - settings.target_lufs:+.2f} LU; ceiling {settings.ceiling_db:+.1f} dBTP. No automatic changes."

    def refresh(self):
        if self.closing or not hasattr(self.owner, "export_view"):
            return
        context = self.context()
        if self.proposal is not None and (
            context is None
            or context[2] != self.proposal_key
            or self.proposal_audio() is not context[0]
        ):
            self.clear_proposal()
            self.message.setText("Choices or rendered input changed. Calculate a new proposal.")
        if self.job is not None and (
            context is None or context[2] != self.job.key or context[0] is not self.job.audio
        ):
            self.job.cancel()
        self.calculate_button.setEnabled(context is not None and self.job is None)
        self.cancel_button.setEnabled(self.job is not None)
        self.apply_button.setEnabled(
            self.proposal is not None and self.job is None and context is not None
        )
        if self.isHidden():
            return
        if context is None:
            self.actual.setText(
                "Render the full chain to measure the current delivery against this request."
            )
            self.written.clear()
            return
        chain = self.owner.chain_result
        delivery = self.owner.export_view.delivery_settings()
        rate = delivery.sample_rate or self.owner.source[1]
        reading = None
        if rate == self.owner.source[1]:
            reading = DeliveryMeasurements(
                rate,
                len(chain.output),
                self.owner.output_view.final_peak,
                chain.true_peak.get(id(chain.output)),
                chain.loudness.get(id(chain.output)),
            )
        else:
            self.owner.export_view.output_view_delivery_report()
            reading = self.owner.export_view.review_measurements
        if reading is None:
            self.calculate_button.setEnabled(False)
            self.actual.setText(
                self.owner.export_view.review_error
                or "Waiting for converted delivery measurements…"
            )
        else:
            request = self.settings()
            self.actual.setText(
                f"Requested {request.target_lufs:+.1f} LUFS / {request.ceiling_db:+.1f} dBTP at {rate / 1000:g} kHz. "
                f"Rendered delivery before quantisation: {format_lufs(reading.lufs)}; sample peak {dbfs(reading.peak):+.2f} dBFS; {format_true_peak(reading.true_peak_db)}. "
                + self.outcome(reading.lufs, reading.true_peak_db, request, reading.peak)
            )
        written = self.owner.export_view.current_written_report()
        self.written.setText(
            f"Last written file: {format_lufs(written.lufs)}; sample peak {written.peak_dbfs:+.2f} dBFS; {format_true_peak(written.true_peak_db)}. "
            + self.outcome(
                written.lufs, written.true_peak_db, self.settings(), 10 ** (written.peak_dbfs / 20)
            )
            if written is not None
            else ""
        )

    def close_jobs(self):
        self.closing = True
        if self.job is not None:
            self.job.cancel()
            self.job.wait()
