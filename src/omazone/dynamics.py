"""Versioned dynamics-mode selection, retaining both compressor recipes."""

from dataclasses import dataclass, field

from . import compressor
from . import multiband as mb


@dataclass(frozen=True)
class DynamicsSettings:
    mode: str = "broadband"
    broadband: compressor.CompressorSettings = field(default_factory=compressor.CompressorSettings)
    multiband: mb.MultibandSettings = field(default_factory=mb.MultibandSettings)


def default_settings(rate=48000):
    return DynamicsSettings(multiband=mb.default_settings(rate))


def settings_from_parameters(parameters, rate=48000):
    if not parameters or (isinstance(parameters, dict) and parameters.get("kind", "compressor-v1") == "compressor-v1"):
        return DynamicsSettings(broadband=compressor.settings_from_parameters(parameters), multiband=mb.default_settings(rate))
    if not isinstance(parameters, dict) or parameters.get("kind") != "dynamics-v2":
        raise ValueError("Unsupported dynamics recipe; saved settings are retained.")
    if set(parameters) != {"kind", "mode", "broadband", "multiband"}:
        raise ValueError("Invalid saved dynamics-mode settings.")
    settings = DynamicsSettings(
        parameters["mode"], compressor.settings_from_parameters(parameters["broadband"]),
        mb.settings_from_parameters(parameters["multiband"]),
    )
    validate_settings(settings, rate)
    return settings


def validate_settings(settings, rate=None):
    if settings.mode not in ("broadband", "three-band"):
        raise ValueError("Dynamics mode must be broadband or three-band.")
    compressor.validate_settings(settings.broadband)
    mb.validate_settings(settings.multiband, rate if settings.mode == "three-band" else None)


def dynamics_parameters(settings):
    if settings.mode == "broadband" and settings.multiband == mb.MultibandSettings():
        return compressor.compressor_parameters(settings.broadband)
    return {
        "kind": "dynamics-v2", "mode": settings.mode,
        "broadband": compressor.compressor_parameters(settings.broadband),
        "multiband": mb.multiband_parameters(settings.multiband),
    }


def render_dynamics(audio, rate, settings, block_size=4096):
    validate_settings(settings, rate)
    if settings.mode == "three-band":
        return mb.render_multiband(audio, rate, settings.multiband, block_size)
    return compressor.render_compressor(audio, rate, settings.broadband, block_size)
