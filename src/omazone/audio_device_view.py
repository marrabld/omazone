"""App-local listening-device preference, deliberately outside project recipes."""

import platform
from collections import Counter

from PySide6 import QtCore, QtWidgets

from . import audio_devices
from .audio_devices import AudioDeviceError, decode_choice, encode_choice

PREFERENCE = "audio/output-device-v1"


def preference_store():
    return QtCore.QSettings("Omazone", "Omazone")


class DeviceSelector(QtWidgets.QComboBox):
    opening = QtCore.Signal()

    def showPopup(self):
        self.opening.emit()
        super().showPopup()


class AudioDeviceView(QtWidgets.QGroupBox):
    def __init__(self, owner):
        super().__init__("Listening device")
        self.setToolTip(
            "Audition only. The device preference does not change processing, projects or exported files."
        )
        self.owner = owner
        self.preferences = preference_store()
        self.choice = None
        self.preference_error = None
        self.devices = None
        self.last_output = None
        self.last_format = None
        self.last_latency = None
        self.last_error = None
        self.stream_state = "Not started"
        try:
            self.choice = decode_choice(self.preferences.value(PREFERENCE))
        except AudioDeviceError as error:
            self.preference_error = str(error)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.selector = DeviceSelector()
        self.selector.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.selector.setMinimumContentsLength(12)
        self.selector.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed
        )
        layout.addWidget(self.selector)
        row = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.setToolTip(
            "Refresh available outputs without switching the current stream."
        )
        self.refresh_button.clicked.connect(self.refresh_devices)
        row.addWidget(self.refresh_button)
        self.copy_button = QtWidgets.QPushButton("Copy diagnostics")
        self.copy_button.clicked.connect(self.copy_diagnostics)
        row.addWidget(self.copy_button)
        layout.addLayout(row)
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.note)
        self.populate()
        self.note.setText(
            self.preference_error
            or "Open the selector or Refresh devices to list outputs. Playback stays at the recording's sample rate."
        )
        self.selector.currentIndexChanged.connect(self.choose_output)
        self.selector.opening.connect(self.refresh_devices)

    def populate(self):
        with QtCore.QSignalBlocker(self.selector):
            self.selector.clear()
            self.selector.addItem("System default", None)
            self.selector.setItemData(
                0,
                "Resolve the system's current default output each time Play starts.",
                QtCore.Qt.ItemDataRole.ToolTipRole,
            )
            counts = Counter(item.key for item in self.devices or ())
            channels = self.owner.source[0].shape[1] if self.owner.source is not None else 1
            for device in self.devices or ():
                index = self.selector.count()
                self.selector.addItem(device.description, device.key)
                enabled = counts[device.key] == 1 and device.channels >= channels
                self.selector.model().item(index).setEnabled(enabled)
                reason = (
                    " Duplicate name; use System default or rename devices."
                    if counts[device.key] != 1
                    else " Not enough output channels."
                    if device.channels < channels
                    else ""
                )
                self.selector.setItemData(
                    index, device.description + reason, QtCore.Qt.ItemDataRole.ToolTipRole
                )
            index = self.selector.findData(self.choice)
            if self.choice is not None and index < 0:
                text = "Unavailable" if self.devices is not None else "Saved; refresh to locate"
                self.selector.addItem(
                    f"{self.choice.name} [{self.choice.hostapi}] ({text})", self.choice
                )
                index = self.selector.count() - 1
                self.selector.model().item(index).setEnabled(False)
            if self.preference_error:
                self.selector.addItem(
                    "Saved output preference unsupported; choose explicitly", "invalid"
                )
                index = self.selector.count() - 1
                self.selector.model().item(index).setEnabled(False)
            self.selector.setCurrentIndex(max(0, index))

    def refresh_devices(self):
        try:
            self.devices = audio_devices.list_outputs()
        except AudioDeviceError as error:
            self.show_error(str(error))
            return
        self.populate()
        message = f"{len(self.devices)} outputs listed. Devices are grouped by backend. "
        message += "Refresh does not switch a running stream. Newly connected devices may require restarting Omazone."
        self.note.setText(self.preference_error or message)

    def source_changed(self):
        # No device queries during project loading or the bundled self-test.
        self.populate()

    def choose_output(self, index):
        if index < 0 or self.selector.currentData() == "invalid":
            return
        if not self.owner.stop():
            self.populate()
            return
        self.choice = self.selector.currentData()
        self.preference_error = None
        self.last_error = None
        message = "Playback stopped. Press Play to check and open the selected output."
        try:
            self.preferences.setValue(PREFERENCE, encode_choice(self.choice))
            self.preferences.sync()
            if self.preferences.status() != QtCore.QSettings.Status.NoError:
                raise OSError("preferences are not writable")
        except Exception:
            message += " Could not remember this choice; it applies for this session only."
        self.note.setText(message)

    def prepare_playback(self, rate, channels):
        if self.preference_error:
            raise AudioDeviceError(self.preference_error)
        return audio_devices.check_output(self.choice, rate, channels)

    def playback_started(self, device, rate, channels, latency):
        self.last_output = device
        self.last_format = (rate, channels)
        self.last_latency = latency
        self.last_error = None
        self.stream_state = "Playing"
        latency_text = (
            f"; reported output latency {latency * 1000:.1f} ms" if latency is not None else ""
        )
        self.note.setText(
            f"Playing through {device.label}: {rate} Hz, {channels} channel(s){latency_text}."
        )

    def playback_stopped(self):
        if self.last_output:
            self.stream_state = "Stopped"
            if not self.last_error:
                self.note.setText(
                    f"Stopped. Last output: {self.last_output.label}. Press Play to check the selected output again."
                )

    def show_error(self, error):
        self.last_error = error
        self.note.setText(error)

    def diagnostics(self):
        selection = (
            f"{self.choice.name} [{self.choice.hostapi}]" if self.choice else "System default"
        )
        lines = [
            "Omazone playback diagnostics",
            f"OS: {platform.system()} {platform.release()} {platform.machine()}",
            f"Selection: {selection}",
            f"Stream: {self.stream_state}",
            f"Last opened output: {self.last_output.description if self.last_output else 'None'}",
            f"Last playback format: {self.last_format[0]} Hz, {self.last_format[1]} channel(s), float32"
            if self.last_format
            else "Last playback format: None",
            f"Reported output latency: {self.last_latency * 1000:.1f} ms"
            if self.last_latency is not None
            else "Reported output latency: unavailable",
            f"Output underflows: {getattr(self.owner, 'playback_dropouts', 0)}",
            f"Last error: {self.last_error or self.preference_error or 'None'}",
            "Audition resampling: none. Audible behaviour still requires a listening check.",
        ]
        return "\n".join(lines)

    def copy_diagnostics(self):
        QtWidgets.QApplication.clipboard().setText(self.diagnostics())
        self.note.setText(
            "Playback diagnostics copied. No recording or project paths are included."
        )
