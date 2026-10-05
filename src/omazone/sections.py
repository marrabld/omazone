"""Named spectral targets and context-aware section matching, without GUI code."""

import json
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path

import numpy as np

from .engine import (
    MatchFilter,
    MatchSettings,
    Spectrum,
    analyse,
    design_match,
    render,
    validate_audio,
)
from .waveform import SampleRegion


@dataclass(frozen=True)
class TargetProfile:
    id: str
    name: str
    spectrum: Spectrum
    sample_rate: int
    region: SampleRegion
    source_name: str


@dataclass(frozen=True)
class SectionAssignment:
    id: str
    name: str
    region: SampleRegion
    target_id: str
    settings: MatchSettings = field(default_factory=MatchSettings)


@dataclass(frozen=True)
class Transition:
    start: int
    end: int
    left_id: str | None
    right_id: str | None


@dataclass(frozen=True)
class SectionCurve:
    section: SectionAssignment
    source: Spectrum
    filter: MatchFilter


@dataclass(frozen=True)
class SectionResult:
    audio: np.ndarray
    curves: tuple[SectionCurve, ...]
    transitions: tuple[Transition, ...]


def check_region(region, length, rate):
    if not all(type(value) is int for value in (region.start, region.end, rate)):
        raise ValueError("Region bounds and sample rate must be integers.")
    if not 0 <= region.start < region.end <= length or rate <= 0:
        raise ValueError("Region must be nonempty and inside the audio.")
    if region.end - region.start < max(2, round(rate * 0.1)):
        raise ValueError("Choose at least 0.1 seconds of representative audio for analysis.")


def capture_target(audio, rate, region, name, target_id, source_name):
    check_region(region, len(audio), rate)
    if not name.strip() or not target_id:
        raise ValueError("Targets need a name and a unique identifier.")
    spectrum = analyse(audio[region.start : region.end], rate)
    return TargetProfile(target_id, name.strip(), spectrum, rate, region, source_name)


def validate_profile(profile):
    if (
        not isinstance(profile.id, str)
        or not profile.id
        or not isinstance(profile.name, str)
        or not profile.name.strip()
    ):
        raise ValueError("Invalid target name or identifier.")
    if not isinstance(profile.source_name, str):
        raise TypeError("Invalid target source metadata.")
    check_region(profile.region, profile.region.end, profile.sample_rate)
    frequency, power = profile.spectrum.frequency, profile.spectrum.power
    if frequency.ndim != 1 or power.shape != frequency.shape or len(frequency) < 2:
        raise ValueError("Invalid target spectrum dimensions.")
    if not np.all(np.isfinite(frequency)) or not np.all(np.isfinite(power)):
        raise ValueError("Target spectrum must be finite.")
    if (
        frequency[0] != 0
        or np.any(np.diff(frequency) <= 0)
        or frequency[-1] > profile.sample_rate / 2
    ):
        raise ValueError("Target frequency grid is invalid.")
    if np.any(power < 0) or np.sum(power) <= 1e-24:
        raise ValueError("Target spectrum is silent or invalid.")


def save_targets(path, profiles):
    entries = []
    ids = set()
    for profile in profiles:
        validate_profile(profile)
        if profile.id in ids:
            raise ValueError("Duplicate target identifiers.")
        ids.add(profile.id)
        entries.append(
            {
                "id": profile.id,
                "name": profile.name,
                "source_name": profile.source_name,
                "sample_rate": profile.sample_rate,
                "region": asdict(profile.region),
                "frequency": profile.spectrum.frequency.tolist(),
                "power": profile.spectrum.power.tolist(),
            }
        )
    Path(path).write_text(
        json.dumps(
            {"format": "omazone-targets", "version": 1, "targets": entries}, allow_nan=False
        ),
        encoding="utf-8",
    )


def load_targets(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            data["format"] != "omazone-targets"
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise ValueError("Unsupported target-profile format or version.")
        if not isinstance(data["targets"], list):
            raise TypeError("Target profiles must be a list.")
        profiles = []
        ids = set()
        for entry in data["targets"]:
            profile = TargetProfile(
                entry["id"],
                entry["name"],
                Spectrum(
                    np.asarray(entry["frequency"], dtype=float),
                    np.asarray(entry["power"], dtype=float),
                ),
                entry["sample_rate"],
                SampleRegion(**entry["region"]),
                entry["source_name"],
            )
            validate_profile(profile)
            if profile.id in ids:
                raise ValueError("Duplicate target identifiers.")
            ids.add(profile.id)
            profiles.append(profile)
        return profiles
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid target-profile JSON.") from error


def validate_sections(sections, targets, length, rate):
    ordered = sorted(sections, key=lambda section: section.region.start)
    ids = set()
    previous_end = 0
    for section in ordered:
        check_region(section.region, length, rate)
        if not section.name.strip() or not section.id or section.id in ids:
            raise ValueError("Sections need names and unique identifiers.")
        ids.add(section.id)
        if section.region.start < previous_end:
            raise ValueError("Section assignments cannot overlap.")
        previous_end = section.region.end
        if section.target_id not in targets:
            raise ValueError(f"Missing target for section {section.name}.")
        validate_profile(targets[section.target_id])
        settings = section.settings
        values = (
            settings.amount,
            settings.smoothing_octaves,
            settings.max_boost_db,
            settings.max_cut_db,
        )
        if (
            not all(np.isfinite(value) for value in values)
            or not 0 <= settings.amount <= 1
            or min(values[1:]) < 0
        ):
            raise ValueError("Invalid section matching settings.")
        if type(settings.taps) is not int or settings.taps < 3 or settings.taps % 2 != 1:
            raise ValueError("Section FIR tap count must be odd and at least three.")
    return ordered


def transition_plan(sections, length, rate, transition_ms):
    if not np.isfinite(transition_ms) or transition_ms < 0:
        raise ValueError("Transition duration must be finite and nonnegative.")
    spans = []
    position = 0
    for section in sorted(sections, key=lambda item: item.region.start):
        if position < section.region.start:
            spans.append((position, section.region.start, None))
        spans.append((section.region.start, section.region.end, section.id))
        position = section.region.end
    if position < length:
        spans.append((position, length, None))
    radius = round(transition_ms * rate / 1000) // 2
    transitions = []
    for left, right in pairwise(spans):
        # Each window consumes at most half of either neighbouring span, so
        # transitions cannot collide inside a short section or a dry gap.
        half = min(radius, (left[1] - left[0]) // 2, (right[1] - right[0]) // 2)
        if half:
            boundary = left[1]
            transitions.append(Transition(boundary - half, boundary + half, left[2], right[2]))
    return tuple(transitions)


def section_curve(audio, rate, section, targets):
    validate_sections([section], targets, len(audio), rate)
    source = analyse(audio[section.region.start : section.region.end], rate)
    spec = design_match(source, targets[section.target_id].spectrum, rate, section.settings)
    return SectionCurve(section, source, spec)


def render_sections(audio, rate, sections, targets, transition_ms=75.0, block_size=4096):
    audio = validate_audio(audio)
    ordered = validate_sections(sections, targets, len(audio), rate)
    transitions = transition_plan(ordered, len(audio), rate, transition_ms)
    output = audio.copy()
    rendered = {}
    curves = []
    for section in ordered:
        curve = section_curve(audio, rate, section, targets)
        curves.append(curve)
        start, end = section.region.start, section.region.end
        cover_start, cover_end = start, end
        for transition in transitions:
            if transition.right_id == section.id:
                cover_start = transition.start
            if transition.left_id == section.id:
                cover_end = transition.end
        delay = curve.filter.latency_samples
        context_start = max(0, cover_start - delay)
        context_end = min(len(audio), cover_end + delay)
        filtered = render(audio[context_start:context_end], curve.filter, block_size)
        covered = filtered[cover_start - context_start : cover_end - context_start].copy()
        rendered[section.id] = (cover_start, covered)
        output[start:end] = covered[start - cover_start : end - cover_start]

    def passage(section_id, start, end):
        if section_id is None:
            return audio[start:end]
        offset, filtered = rendered[section_id]
        return filtered[start - offset : end - offset]

    for transition in transitions:
        start, end = transition.start, transition.end
        # Raised-cosine amplitude weights remain complementary, including for
        # highly correlated/identical filtered signals. Both paths are aligned.
        weight = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, end - start)))[:, None]
        output[start:end] = (
            passage(transition.left_id, start, end) * (1 - weight)
            + passage(transition.right_id, start, end) * weight
        )
    return SectionResult(output, tuple(curves), transitions)
