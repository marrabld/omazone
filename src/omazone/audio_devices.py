"""PortAudio output discovery and preflight, without Qt or stream operations.

Indices are session-local. Remember host API and device name instead, and refuse
ambiguous/missing names rather than picking an arbitrary device after a restart.
"""

import json
from dataclasses import asdict, dataclass


class AudioDeviceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceKey:
    hostapi: str
    name: str


@dataclass(frozen=True)
class OutputDevice:
    index: int
    key: DeviceKey
    channels: int
    default_rate: float

    @property
    def label(self):
        return f"{self.key.name} [{self.key.hostapi}]"

    @property
    def description(self):
        return f"{self.label}; {self.channels} outputs; reported default {self.default_rate:g} Hz"


def backend():
    # No PortAudio initialisation during Window construction or --selftest.
    import sounddevice

    return sounddevice


def encode_choice(choice):
    return json.dumps({"version": 1, "device": asdict(choice) if choice else None})


def decode_choice(raw):
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "device"}
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise ValueError
        if data["device"] is None:
            return None
        item = data["device"]
        if not isinstance(item, dict) or set(item) != {"hostapi", "name"}:
            raise ValueError
        if any(not isinstance(value, str) or not value.strip() for value in item.values()):
            raise ValueError
        return DeviceKey(**item)
    except (TypeError, ValueError) as error:
        raise AudioDeviceError(
            "Saved output preference is unsupported. Choose an output explicitly; "
            "the saved preference has not been replaced."
        ) from error


def _device(info, apis):
    return OutputDevice(
        int(info["index"]),
        DeviceKey(apis[info["hostapi"]]["name"], info["name"]),
        int(info["max_output_channels"]),
        float(info["default_samplerate"]),
    )


def list_outputs(api=None):
    try:
        api = backend() if api is None else api
        apis = api.query_hostapis()
        devices = [
            _device(item, apis) for item in api.query_devices() if item["max_output_channels"] > 0
        ]
        return tuple(
            sorted(devices, key=lambda item: (item.key.hostapi, item.key.name, item.index))
        )
    except Exception as error:  # backend errors must not escape into Qt signals
        raise AudioDeviceError(
            f"Could not list audio outputs: {error}. Refresh devices or restart Omazone."
        ) from error


def resolve_output(choice, api=None):
    api = backend() if api is None else api
    if choice is None:
        try:
            device = _device(api.query_devices(kind="output"), api.query_hostapis())
            if device.channels < 1:
                raise ValueError("System default has no output channels")
            return device
        except Exception as error:
            raise AudioDeviceError(
                f"System default output is unavailable: {error}. "
                "Choose an output in Output > Listening device."
            ) from error
    matches = [item for item in list_outputs(api) if item.key == choice]
    if not matches:
        raise AudioDeviceError(
            f"Selected output {choice.name} [{choice.hostapi}] is unavailable. "
            "Reconnect it and refresh, or explicitly choose another output. No fallback was used."
        )
    if len(matches) != 1:
        raise AudioDeviceError(
            f"More than one output is named {choice.name} [{choice.hostapi}]. "
            "Rename the devices in system audio settings or explicitly choose System default. "
            "Omazone cannot safely distinguish them by name."
        )
    return matches[0]


def check_output(choice, rate, channels, api=None):
    """Resolve afresh and validate the EXACT format used to open the next stream."""
    if type(rate) is not int or rate <= 0 or type(channels) is not int or channels not in (1, 2):
        raise AudioDeviceError(
            "Audition requires mono or stereo audio at a positive integer sample rate."
        )
    api = backend() if api is None else api
    device = resolve_output(choice, api)
    if channels > device.channels:
        raise AudioDeviceError(
            f"{device.label} has {device.channels} output channel(s), but this recording "
            f"needs {channels}. Choose another output."
        )
    try:
        api.check_output_settings(
            device=device.index, samplerate=rate, channels=channels, dtype="float32"
        )
        # Detect index changes during preflight. Do not open another physical
        # device just because it inherited the old device's numeric index.
        current = _device(api.query_devices(device=device.index), api.query_hostapis())
        if current != device:
            raise AudioDeviceError(
                "Audio output changed while preparing playback. Refresh and retry."
            )
    except AudioDeviceError:
        raise
    except Exception as error:
        raise AudioDeviceError(
            f"{device.label} could not play {rate} Hz, {channels} channel(s), float32: {error}. "
            f"It reports a default rate of {device.default_rate:g} Hz; that is not a complete "
            "list of supported rates. Check the device's system audio settings or choose "
            "another output. Omazone does not resample audition or fall back silently."
        ) from error
    return device
