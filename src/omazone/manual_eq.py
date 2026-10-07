"""Causal bell bands with a shared region and stereo-linked paths.

The IIR runs with continuous history across the recording. Within a named region
its wet output fades in/out with complementary amplitude weights. Outside the
region samples are copied exactly, and the file retains its original length.
"""

from dataclasses import asdict, dataclass

import numpy as np
from scipy import signal

from .engine import validate_audio


@dataclass(frozen=True)
class BellSettings:
    frequency: float = 2400.0
    gain_db: float = 0.0
    q: float = 1.0
    region_id: str | None = None
    transition_ms: float = 75.0
    enabled: bool = True


MAX_BANDS = 12


@dataclass(frozen=True)
class EQSettings:
    bands: tuple[BellSettings, ...] = ()
    region_id: str | None = None
    transition_ms: float = 75.0


def eq_parameters(settings):
    return {
        "kind": "multi-bell-v2",
        "bands": [
            {
                key: value
                for key, value in asdict(band).items()
                if key not in ("region_id", "transition_ms")
            }
            for band in settings.bands
        ],
        "region_id": settings.region_id,
        "transition_ms": settings.transition_ms,
    }


def eq_from_parameters(parameters):
    if not parameters:
        return EQSettings()
    if set(parameters) == {"bands"} and isinstance(parameters["bands"], list):
        # Early project files allowed a reserved band list before EQ could render.
        # Import it only when its per-band scopes agree with the shared scope.
        try:
            bands = tuple(BellSettings(**band) for band in parameters["bands"])
        except TypeError as error:
            raise ValueError("Invalid saved EQ band settings.") from error
        scopes = {(band.region_id, band.transition_ms) for band in bands}
        if len(scopes) > 1:
            raise ValueError("Saved EQ bands have different scopes; choose a shared passage.")
        region_id, transition_ms = next(iter(scopes), (None, 75.0))
        return EQSettings(bands, region_id, transition_ms)
    if parameters.get("kind", "bell-v1") == "bell-v1":
        if set(parameters) - {"kind", "band"} or not isinstance(parameters.get("band"), dict):
            raise ValueError("Invalid saved EQ band settings.")
        try:
            band = BellSettings(**parameters["band"])
        except TypeError as error:
            raise ValueError("Invalid saved EQ band settings.") from error
        return EQSettings((band,), band.region_id, band.transition_ms)
    if parameters.get("kind") != "multi-bell-v2" or set(parameters) != {
        "kind",
        "bands",
        "region_id",
        "transition_ms",
    }:
        raise ValueError("Unrecognised saved EQ settings. They have not been replaced.")
    bands = parameters["bands"]
    if not isinstance(bands, list) or len(bands) > MAX_BANDS:
        raise ValueError("EQ supports at most 12 bands.")
    try:
        if any(
            not isinstance(band, dict) or set(band) - {"frequency", "gain_db", "q", "enabled"}
            for band in bands
        ):
            raise ValueError("Invalid manual EQ band settings.")
        return EQSettings(
            tuple(BellSettings(**band) for band in bands),
            parameters["region_id"],
            parameters["transition_ms"],
        )
    except TypeError as error:
        raise ValueError("Invalid manual EQ band settings.") from error


def validate_eq(settings, rate, regions=None):
    if len(settings.bands) > MAX_BANDS or not isinstance(settings.region_id, (str, type(None))):
        raise ValueError("Invalid manual EQ scope or band count.")
    if (
        not isinstance(settings.transition_ms, (int, float, np.number))
        or isinstance(settings.transition_ms, (bool, np.bool_))
        or not np.isfinite(settings.transition_ms)
        or not 0 <= settings.transition_ms <= 5000
    ):
        raise ValueError("EQ transition must be between zero and 5000 ms.")
    if (
        regions is not None
        and settings.region_id is not None
        and settings.region_id not in {item.id for item in regions}
    ):
        raise ValueError("The EQ region is missing. Choose an existing region or Whole recording.")
    for band in settings.bands:
        validate_settings(band, rate)
        if type(band.enabled) is not bool:
            raise ValueError("Invalid EQ band bypass.")


def settings_from_parameters(parameters):
    if not parameters:
        return BellSettings()
    if parameters.get("kind", "bell-v1") != "bell-v1" or "bands" in parameters:
        raise ValueError("This version supports one bell band. Saved EQ settings are retained.")
    if set(parameters) - {"kind", "band"} or not isinstance(parameters.get("band"), dict):
        raise ValueError("Unrecognised saved EQ settings. They have not been replaced.")
    try:
        return BellSettings(**parameters["band"])
    except TypeError as error:
        raise ValueError("Invalid manual EQ band settings.") from error


def validate_settings(settings, rate, regions=None):
    if any(
        not isinstance(value, (int, float, np.number)) or isinstance(value, (bool, np.bool_))
        for value in (settings.frequency, settings.gain_db, settings.q, settings.transition_ms)
    ):
        raise ValueError("EQ settings must be numeric.")
    if not all(
        np.isfinite(value)
        for value in (settings.frequency, settings.gain_db, settings.q, settings.transition_ms)
    ):
        raise ValueError("EQ settings must be finite.")
    if not 20 <= settings.frequency < rate / 2:
        raise ValueError("EQ frequency must be at least 20 Hz and below Nyquist.")
    if not -18 <= settings.gain_db <= 18 or not 0.1 <= settings.q <= 20:
        raise ValueError("EQ supports +/-18 dB gain and Q from 0.1 to 20.")
    if not 0 <= settings.transition_ms <= 5000:
        raise ValueError("EQ transition must be between zero and 5000 ms.")
    if (
        regions is not None
        and settings.region_id is not None
        and settings.region_id not in {item.id for item in regions}
    ):
        raise ValueError("The EQ region is missing. Choose an existing region or Whole recording.")


def bell_sos(settings, rate):
    validate_settings(settings, rate)
    a = 10 ** (settings.gain_db / 40)
    omega = 2 * np.pi * settings.frequency / rate
    alpha = np.sin(omega) / (2 * settings.q)
    numerator = np.asarray([1 + alpha * a, -2 * np.cos(omega), 1 - alpha * a])
    denominator = np.asarray([1 + alpha / a, -2 * np.cos(omega), 1 - alpha / a])
    return np.concatenate((numerator / denominator[0], denominator / denominator[0]))[None, :]


class BellProcessor:
    """Readable block processor; no lookahead, no fixed latency, infinite IIR tail.

    The offline renderer does not append the infinite tail. Coefficient changes
    reset state and are offline operations; smooth region gain is applied afterward.
    """

    latency_samples = 0

    def prepare(self, sample_rate, channels, max_block_size):
        if min(sample_rate, channels, max_block_size) <= 0:
            raise ValueError("Preparation values must be positive.")
        self.sample_rate, self.channels, self.max_block_size = sample_rate, channels, max_block_size
        self.set_settings(BellSettings(frequency=min(2400, sample_rate / 4)))

    def set_settings(self, settings):
        self.sos = bell_sos(settings, self.sample_rate)
        self.reset()

    def reset(self):
        self.state = np.zeros((1, 2, self.channels))

    def process_block(self, block):
        block = np.asarray(block, dtype=float)
        if (
            block.ndim != 2
            or block.shape[1] != self.channels
            or not 0 < len(block) <= self.max_block_size
        ):
            raise ValueError("Block differs from the prepared EQ format or size.")
        output, self.state = signal.sosfilt(self.sos, block, axis=0, zi=self.state)
        return output


def region_envelope(length, rate, settings, regions):
    """Wet weight; transitions lie inside the half-open region, never outside it."""
    if settings.region_id is None:
        return None
    region = next(item.bounds for item in regions if item.id == settings.region_id)
    if not 0 <= region.start < region.end <= length:
        raise ValueError("EQ region is outside the recording.")
    weights = np.ones(region.end - region.start)
    fade = min(round(settings.transition_ms * rate / 1000), len(weights) // 2)
    if fade:
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, fade))
        if region.start > 0:
            weights[:fade] = ramp
        if region.end < length:
            weights[-fade:] = ramp[::-1]
    return region, weights


def render_eq(audio, rate, settings, regions=(), block_size=4096):
    audio = validate_audio(audio)
    if isinstance(settings, BellSettings):
        settings = EQSettings((settings,), settings.region_id, settings.transition_ms)
    validate_eq(settings, rate, regions)
    if type(block_size) is not int or block_size <= 0:
        raise ValueError("EQ block size must be a positive integer.")
    active = [band for band in settings.bands if band.enabled and band.gain_db != 0]
    if not active:
        return audio.copy()
    sos = np.vstack([bell_sos(band, rate) for band in active])
    state = np.zeros((len(sos), 2, audio.shape[1]))
    wet = np.empty_like(audio)
    for start in range(0, len(audio), block_size):
        block = audio[start : start + block_size]
        wet[start : start + len(block)], state = signal.sosfilt(sos, block, axis=0, zi=state)
    assignment = region_envelope(len(audio), rate, settings, regions)
    if assignment is None:
        return wet
    bounds, weights = assignment
    output = audio.copy()
    output[bounds.start : bounds.end] += (
        wet[bounds.start : bounds.end] - audio[bounds.start : bounds.end]
    ) * weights[:, None]
    return output


def frequency_response(settings, rate):
    if isinstance(settings, EQSettings):
        active = [band for band in settings.bands if band.enabled and band.gain_db != 0]
        if not active:
            frequency = np.linspace(0, rate / 2, 8192, endpoint=False)
            return frequency, np.zeros_like(frequency)
        sos = np.vstack([bell_sos(band, rate) for band in active])
    else:
        sos = bell_sos(settings, rate)
    frequency, response = signal.sosfreqz(sos, worN=8192, fs=rate)
    return frequency, 20 * np.log10(np.maximum(np.abs(response), 1e-12))
