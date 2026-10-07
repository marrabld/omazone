"""Explicit final gain and sample-peak measurements; no peak limiting."""

from dataclasses import asdict, dataclass

import numpy as np

from .engine import validate_audio


@dataclass(frozen=True)
class OutputGainSettings:
    gain_db: float = 0.0


def settings_from_parameters(parameters):
    if not parameters:
        return OutputGainSettings()
    if not isinstance(parameters, dict) or parameters.get("kind") != "output-gain-v1":
        raise ValueError("Unsupported output recipe; saved settings are retained.")
    if set(parameters) != {"kind", "gain_db"}:
        raise ValueError("Invalid saved output gain settings.")
    return OutputGainSettings(parameters["gain_db"])


def output_parameters(settings):
    return {"kind": "output-gain-v1", **asdict(settings)}


def validate_settings(settings):
    gain = settings.gain_db
    if (
        not isinstance(gain, (int, float, np.number))
        or isinstance(gain, (bool, np.bool_))
        or not np.isfinite(gain)
        or not -36 <= gain <= 18
    ):
        raise ValueError("Output gain must be a finite number from -36 to +18 dB.")


def apply_output_gain(audio, settings):
    audio = validate_audio(audio)
    validate_settings(settings)
    if settings.gain_db == 0:
        return audio.copy()
    output = audio * 10 ** (settings.gain_db / 20)
    if not np.all(np.isfinite(output)):
        raise ValueError("Output gain produced non-finite audio.")
    return output


def sample_peak(audio):
    """Raw absolute sample maximum; > 1.0 means above 0 dBFS in float audio."""
    return float(np.max(np.abs(validate_audio(audio))))


def sample_peak_db(audio):
    peak = sample_peak(audio)
    return 20 * np.log10(peak) if peak else float("-inf")
