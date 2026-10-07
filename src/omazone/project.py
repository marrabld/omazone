"""Versioned non-destructive project recipes. No Qt or baked audio in this model."""

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from uuid import uuid4

import numpy as np
import soundfile as sf

from .clipping import ClipInterval, ClipReport, DetectionSettings, detect_clipping
from .declipping import RepairResult, RepairSettings, repair_clipping
from .engine import MatchFilter, MatchSettings, Spectrum, analyse
from .sections import (
    SectionAssignment,
    SectionCurve,
    TargetProfile,
    validate_profile,
    validate_sections,
)
from .waveform import PeakIndex, SampleRegion

STAGES = ("repair", "match", "eq", "dynamics", "output")


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class AudioReference:
    path: str
    sample_rate: int
    frames: int
    channels: int
    sha256: str

    @classmethod
    def from_path(cls, path):
        path = Path(path).resolve()
        info = sf.info(path)
        return cls(str(path), info.samplerate, info.frames, info.channels, fingerprint(path))

    def verify(self):
        path = Path(self.path)
        if not path.is_file():
            return "missing"
        try:
            info = sf.info(path)
            if (info.samplerate, info.frames, info.channels) != (
                self.sample_rate,
                self.frames,
                self.channels,
            ):
                return "incompatible"
            return "ready" if fingerprint(path) == self.sha256 else "changed"
        except (OSError, RuntimeError):
            return "unreadable"


@dataclass(frozen=True)
class NamedRegion:
    id: str
    name: str
    bounds: SampleRegion


@dataclass(frozen=True)
class RepairOperation:
    id: str
    region: SampleRegion
    detection: DetectionSettings
    channel_settings: tuple[DetectionSettings, ...]
    accepted: tuple[ClipInterval, ...]
    settings: RepairSettings
    method: str = "hermite-v1"

    def report(self, audio):
        settings = self.channel_settings or (self.detection,)
        reports = [detect_clipping(audio, self.region, item) for item in settings]
        return ClipReport(
            self.region,
            self.detection,
            tuple(item for report in reports for item in report.candidates),
            tuple(item for report in reports for item in report.overloads),
            tuple(item for report in reports for item in report.stats),
            self.channel_settings,
        )


@dataclass
class StageState:
    bypassed: bool = False
    parameters: dict = field(default_factory=dict)


@dataclass
class MatchCalibration:
    config_key: str
    input_key: str
    whole: MatchFilter | None = None
    sections: tuple[SectionCurve, ...] = ()


@dataclass
class Project:
    id: str = field(default_factory=lambda: uuid4().hex)
    source: AudioReference | None = None
    reference: AudioReference | None = None
    reference_target: TargetProfile | None = None
    regions: list[NamedRegion] = field(default_factory=list)
    targets: dict[str, TargetProfile] = field(default_factory=dict)
    sections: list[SectionAssignment] = field(default_factory=list)
    repairs: list[RepairOperation] = field(default_factory=list)
    matching: MatchSettings = field(default_factory=MatchSettings)
    match_mode: str = "none"
    transition_ms: float = 75.0
    stages: dict[str, StageState] = field(
        default_factory=lambda: {
            name: StageState(bypassed=name not in ("repair", "match")) for name in STAGES
        }
    )
    calibration: MatchCalibration | None = None
    view: dict = field(default_factory=dict)
    revision: int = 0
    dirty: bool = False
    needs_render: bool = True

    def touch(self):
        self.revision += 1
        self.dirty = True
        self.needs_render = True

    def import_sections(self, sections):
        regions = {item.id: item for item in self.regions}
        self.sections = list(sections)
        for item in sections:
            regions[item.id] = NamedRegion(item.id, item.name, item.region)
        self.regions = list(regions.values())

    def input_key(self):
        return digest_json(
            {
                "source": self.source.sha256 if self.source else None,
                "repairs": [
                    {key: value for key, value in asdict(item).items() if key != "id"}
                    for item in self.repairs
                ],
                "bypassed": self.stages["repair"].bypassed,
            }
        )

    def config_key(self):
        if self.match_mode == "whole":
            config = {
                "mode": "whole",
                "settings": asdict(self.matching),
                "target": spectrum_dict(self.reference_target.spectrum)
                if self.reference_target
                else None,
            }
        else:
            config = {
                "mode": self.match_mode,
                "sections": [
                    {
                        "id": item.id,
                        "region": asdict(item.region),
                        "target_id": item.target_id,
                        "settings": asdict(item.settings),
                    }
                    for item in self.sections
                ],
                "transition_ms": self.transition_ms,
                "targets": {
                    key: spectrum_dict(self.targets[key].spectrum)
                    for key in sorted({item.target_id for item in self.sections})
                },
            }
        return digest_json(config)

    @property
    def needs_reanalysis(self):
        return (
            self.calibration is None
            or self.calibration.config_key != self.config_key()
            or self.calibration.input_key != self.input_key()
        )

    @property
    def can_render_saved_match(self):
        return self.calibration is not None and self.calibration.config_key == self.config_key()

    def relink(self, kind, path):
        if kind not in ("source", "reference"):
            raise ValueError("Unknown audio reference.")
        old = getattr(self, kind)
        if old is None:
            raise ValueError("There is no saved audio reference to relink.")
        chosen = replace(old, path=str(Path(path).resolve()))
        state = chosen.verify()
        if state != "ready":
            raise ValueError(
                f"Selected recording is {state}; saved choices are retained. Choose the original file."
            )
        setattr(self, kind, chosen)
        self.dirty = True


def digest_json(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def spectrum_dict(spectrum):
    return {"frequency": spectrum.frequency.tolist(), "power": spectrum.power.tolist()}


def target_dict(target):
    return {
        "id": target.id,
        "name": target.name,
        "spectrum": spectrum_dict(target.spectrum),
        "sample_rate": target.sample_rate,
        "region": asdict(target.region),
        "source_name": target.source_name,
    }


def parse_target(data, minimum_seconds=0.1):
    target = TargetProfile(
        data["id"],
        data["name"],
        parse_spectrum(data["spectrum"]),
        data["sample_rate"],
        SampleRegion(**data["region"]),
        data["source_name"],
    )
    validate_profile(target, minimum_seconds)
    return target


def parse_spectrum(data):
    spectrum = Spectrum(
        np.asarray(data["frequency"], dtype=float), np.asarray(data["power"], dtype=float)
    )
    if (
        spectrum.frequency.ndim != 1
        or len(spectrum.frequency) < 2
        or spectrum.power.shape != spectrum.frequency.shape
    ):
        raise ValueError("Invalid saved spectrum shape.")
    if (
        not np.all(np.isfinite(spectrum.frequency))
        or not np.all(np.isfinite(spectrum.power))
        or np.any(spectrum.power < 0)
    ):
        raise ValueError("Invalid saved spectrum values.")
    if (
        spectrum.frequency[0] != 0
        or np.any(np.diff(spectrum.frequency) <= 0)
        or np.sum(spectrum.power) <= 0
    ):
        raise ValueError("Invalid saved spectrum grid or energy.")
    return spectrum


def filter_dict(spec):
    return {
        "coefficients": spec.coefficients.tolist(),
        "frequency": spec.frequency.tolist(),
        "requested_db": spec.requested_db.tolist(),
        "sample_rate": spec.sample_rate,
    }


def parse_filter(data, rate):
    spec = MatchFilter(
        np.asarray(data["coefficients"], dtype=float),
        np.asarray(data["frequency"], dtype=float),
        np.asarray(data["requested_db"], dtype=float),
        data["sample_rate"],
    )
    if (
        spec.sample_rate != rate
        or spec.coefficients.ndim != 1
        or not len(spec.coefficients)
        or len(spec.coefficients) % 2 != 1
    ):
        raise ValueError("Invalid saved matching filter.")
    if (
        spec.frequency.ndim != 1
        or spec.requested_db.shape != spec.frequency.shape
        or len(spec.frequency) < 2
    ):
        raise ValueError("Invalid saved filter response.")
    if not all(
        np.all(np.isfinite(item)) for item in (spec.coefficients, spec.frequency, spec.requested_db)
    ):
        raise ValueError("Saved filter must be finite.")
    if (
        spec.frequency[0] != 0
        or spec.frequency[-1] > rate / 2
        or np.any(np.diff(spec.frequency) <= 0)
        or not np.allclose(spec.coefficients, spec.coefficients[::-1], atol=1e-12)
    ):
        raise ValueError("Saved filter grid or linear-phase symmetry is invalid.")
    return spec


def validate_project(project):
    if (
        not isinstance(project.id, str)
        or not project.id
        or project.match_mode not in ("none", "whole", "sections")
    ):
        raise ValueError("Invalid project identifier or matching mode.")
    for asset in (project.source, project.reference):
        if asset is not None:
            if (
                not isinstance(asset.path, str)
                or not asset.path
                or any(
                    type(value) is not int or value <= 0
                    for value in (asset.sample_rate, asset.frames, asset.channels)
                )
            ):
                raise ValueError("Invalid audio reference metadata.")
            if (
                asset.channels not in (1, 2)
                or len(asset.sha256) != 64
                or any(char not in "0123456789abcdef" for char in asset.sha256)
            ):
                raise ValueError("Invalid source format or fingerprint.")
    if set(project.stages) != set(STAGES):
        raise ValueError("Project must define every known stage.")
    for state in project.stages.values():
        if type(state.bypassed) is not bool or not isinstance(state.parameters, dict):
            raise ValueError("Invalid stage state.")
        json.dumps(state.parameters, allow_nan=False)
    if project.stages["eq"].parameters:
        from .manual_eq import eq_from_parameters, validate_eq

        validate_eq(
            eq_from_parameters(project.stages["eq"].parameters),
            project.source.sample_rate if project.source else 48000,
            project.regions,
        )
    if not np.isfinite(project.transition_ms) or project.transition_ms < 0:
        raise ValueError("Invalid transition duration.")
    values = (
        project.matching.amount,
        project.matching.smoothing_octaves,
        project.matching.max_boost_db,
        project.matching.max_cut_db,
    )
    if (
        not all(np.isfinite(value) for value in values)
        or not 0 <= values[0] <= 1
        or min(values[1:]) < 0
        or type(project.matching.taps) is not int
        or project.matching.taps < 3
        or project.matching.taps % 2 != 1
    ):
        raise ValueError("Invalid whole-song matching settings.")
    if project.source is None and (
        project.regions or project.sections or project.repairs or project.calibration
    ):
        raise ValueError("Regions and operations require source metadata.")
    ids = set()
    for region in project.regions:
        if (
            not region.id
            or region.id in ids
            or not region.name.strip()
            or type(region.bounds.start) is not int
            or type(region.bounds.end) is not int
            or not 0 <= region.bounds.start < region.bounds.end <= project.source.frames
        ):
            raise ValueError("Invalid or duplicate named region.")
        ids.add(region.id)
    for key, target in project.targets.items():
        if key != target.id:
            raise ValueError("Target identifiers differ.")
        validate_profile(target)
    if project.reference_target is not None:
        validate_profile(project.reference_target, minimum_seconds=0)
    if project.source:
        validate_sections(
            project.sections, project.targets, project.source.frames, project.source.sample_rate
        )
        named = {item.id: item for item in project.regions}
        for section in project.sections:
            if section.id not in named or named[section.id].bounds != section.region:
                raise ValueError("Section assignment and named region disagree.")
    occupied = set()
    repair_ids = set()
    for operation in project.repairs:
        if not operation.id or operation.id in repair_ids or operation.method != "hermite-v1":
            raise ValueError("Unknown repair method or duplicate operation.")
        repair_ids.add(operation.id)
        if (
            not np.isfinite(operation.settings.max_run_ms)
            or operation.settings.max_run_ms <= 0
            or type(operation.settings.context_samples) is not int
            or not 3 <= operation.settings.context_samples <= 64
            or not np.isfinite(operation.settings.max_peak_ratio)
            or operation.settings.max_peak_ratio < 1
        ):
            raise ValueError("Invalid saved reconstruction settings.")
        for settings in (operation.detection, *operation.channel_settings):
            if (
                not all(
                    np.isfinite(value)
                    for value in (settings.positive, settings.negative, settings.tolerance)
                )
                or settings.positive <= 0
                or settings.negative >= 0
                or not 0 < settings.tolerance < min(settings.positive, -settings.negative)
                or type(settings.minimum_run) is not int
                or settings.minimum_run < 2
            ):
                raise ValueError("Invalid saved detector settings.")
            if settings.channel is not None and (
                type(settings.channel) is not int
                or not 0 <= settings.channel < project.source.channels
            ):
                raise ValueError("Invalid saved repair channel.")
        if not 0 <= operation.region.start < operation.region.end <= project.source.frames:
            raise ValueError("Repair region lies outside the source.")
        for item in operation.accepted:
            if (
                not 0 <= item.channel < project.source.channels
                or not operation.region.start <= item.start < item.end <= operation.region.end
            ):
                raise ValueError("Accepted repair interval is outside its source/selection.")
            if item.polarity not in ("positive", "negative") or not np.isfinite(item.level):
                raise ValueError("Invalid repair interval.")
            # Reject overlapping operations, not merely identical interval tuples.
            for channel, start, end in occupied:
                if channel == item.channel and max(start, item.start) < min(end, item.end):
                    raise ValueError("Repair operations overlap.")
            occupied.add((item.channel, item.start, item.end))
    if not isinstance(project.view, dict):
        raise ValueError("View preferences must be an object.")
    json.dumps(project.view, allow_nan=False)
    if (
        type(project.view.get("active_tool", 0)) is not int
        or project.view.get("active_tool", 0) < 0
    ):
        raise ValueError("Invalid saved tool selection.")
    if type(project.view.get("loop_enabled", False)) is not bool:
        raise ValueError("Invalid saved loop state.")
    for key, allowed in (
        ("viewer_mode", ("waveform", "spectrum", "both")),
        ("viewer_signal", ("original", "input", "output")),
    ):
        if key in project.view and project.view[key] not in allowed:
            raise ValueError("Unknown saved viewer preference.")
    for key in ("viewer_split", "reference_split"):
        sizes = project.view.get(key)
        if sizes is not None and (
            not isinstance(sizes, list)
            or len(sizes) != 2
            or any(type(item) is not int or item < 0 for item in sizes)
        ):
            raise ValueError("Invalid saved viewer panel sizes.")
    modes = project.view.get("viewer_tool_modes", {})
    if not isinstance(modes, dict) or any(
        not isinstance(key, str) or value not in ("waveform", "spectrum", "both")
        for key, value in modes.items()
    ):
        raise ValueError("Invalid saved tool-specific viewer modes.")
    if type(project.view.get("matching_overview", False)) is not bool:
        raise ValueError("Invalid saved matching overview preference.")
    reference_rate = (
        project.reference.sample_rate
        if project.reference
        else (project.reference_target.sample_rate if project.reference_target else None)
    )
    reference_length = (
        project.reference.frames
        if project.reference
        else (project.reference_target.region.end if project.reference_target else None)
    )
    reference_bounds = project.view.get("reference_selection")
    if reference_bounds is not None and (
        reference_length is None
        or not isinstance(reference_bounds, list)
        or len(reference_bounds) != 2
        or any(type(item) is not int for item in reference_bounds)
        or not 0 <= reference_bounds[0] < reference_bounds[1] <= reference_length
    ):
        raise ValueError("Saved reference selection is invalid.")
    reference_zoom = project.view.get("reference_zoom")
    if reference_zoom is not None and (
        reference_length is None
        or not isinstance(reference_zoom, list)
        or len(reference_zoom) != 2
        or not all(isinstance(item, (int, float)) and np.isfinite(item) for item in reference_zoom)
        or not 0
        <= reference_zoom[0]
        < reference_zoom[1]
        <= reference_length / reference_rate + 1 / reference_rate
    ):
        raise ValueError("Saved reference zoom is invalid.")
    if project.source:
        for key in ("selection", "loop_region"):
            bounds = project.view.get(key)
            if bounds is not None and (
                not isinstance(bounds, list)
                or len(bounds) != 2
                or any(type(item) is not int for item in bounds)
                or not 0 <= bounds[0] < bounds[1] <= project.source.frames
            ):
                raise ValueError("Saved view bounds lie outside the source.")
        position = project.view.get("position", 0)
        if type(position) is not int or not 0 <= position <= project.source.frames:
            raise ValueError("Saved cursor lies outside the source.")
        zoom = project.view.get("zoom")
        if zoom is not None and (
            not isinstance(zoom, list)
            or len(zoom) != 2
            or not all(isinstance(item, (int, float)) and np.isfinite(item) for item in zoom)
            or not 0
            <= zoom[0]
            < zoom[1]
            <= project.source.frames / project.source.sample_rate + 1 / project.source.sample_rate
        ):
            raise ValueError("Invalid saved waveform zoom.")
    if project.calibration is not None:
        for key in (project.calibration.config_key, project.calibration.input_key):
            if not isinstance(key, str) or len(key) != 64:
                raise ValueError("Invalid matching calibration signature.")
        if project.calibration.whole is not None:
            parse_filter(filter_dict(project.calibration.whole), project.source.sample_rate)
        for curve in project.calibration.sections:
            parse_filter(filter_dict(curve.filter), project.source.sample_rate)
            parse_spectrum(spectrum_dict(curve.source))
        if project.calibration.config_key == project.config_key():
            if project.match_mode == "whole" and project.calibration.whole is None:
                raise ValueError("Whole-song calibration has no filter.")
            if project.match_mode == "sections" and {
                item.section.id for item in project.calibration.sections
            } != {item.id for item in project.sections}:
                raise ValueError("Saved section calibration IDs differ.")


def project_dict(project, path):
    validate_project(project)
    directory = Path(path).resolve().parent

    def asset_dict(asset):
        if asset is None:
            return None
        data = asdict(asset)
        data["path"] = os.path.relpath(asset.path, directory)
        return data

    calibration = None
    if project.calibration:
        calibration = {
            "config_key": project.calibration.config_key,
            "input_key": project.calibration.input_key,
            "whole": filter_dict(project.calibration.whole) if project.calibration.whole else None,
            "sections": [
                {
                    "section": asdict(curve.section),
                    "source": spectrum_dict(curve.source),
                    "filter": filter_dict(curve.filter),
                }
                for curve in project.calibration.sections
            ],
        }
    return {
        "format": "omazone-project",
        "version": 1,
        "id": project.id,
        "source": asset_dict(project.source),
        "reference": asset_dict(project.reference),
        "reference_target": target_dict(project.reference_target)
        if project.reference_target
        else None,
        "regions": [asdict(item) for item in project.regions],
        "targets": [target_dict(item) for item in project.targets.values()],
        "sections": [asdict(item) for item in project.sections],
        "repairs": [asdict(item) for item in project.repairs],
        "matching": asdict(project.matching),
        "match_mode": project.match_mode,
        "transition_ms": project.transition_ms,
        "stages": {key: asdict(value) for key, value in project.stages.items()},
        "calibration": calibration,
        "view": project.view,
    }


def save_project(path, project):
    data = json.dumps(project_dict(project, path), allow_nan=False, indent=2)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_project(path):
    directory = Path(path).resolve().parent
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            data["format"] != "omazone-project"
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise ValueError("Unsupported project format/version.")

        def asset(entry):
            if entry is None:
                return None
            return AudioReference(**{**entry, "path": str((directory / entry["path"]).resolve())})

        targets = [parse_target(item) for item in data["targets"]]
        if len({item.id for item in targets}) != len(targets):
            raise ValueError("Duplicate target identifiers.")
        project = Project(
            id=data["id"],
            source=asset(data["source"]),
            reference=asset(data["reference"]),
            reference_target=parse_target(data["reference_target"], minimum_seconds=0)
            if data["reference_target"]
            else None,
            targets={item.id: item for item in targets},
            regions=[
                NamedRegion(item["id"], item["name"], SampleRegion(**item["bounds"]))
                for item in data["regions"]
            ],
            sections=[
                SectionAssignment(
                    item["id"],
                    item["name"],
                    SampleRegion(**item["region"]),
                    item["target_id"],
                    MatchSettings(**item["settings"]),
                )
                for item in data["sections"]
            ],
            matching=MatchSettings(**data["matching"]),
            match_mode=data["match_mode"],
            transition_ms=data["transition_ms"],
            stages={key: StageState(**value) for key, value in data["stages"].items()},
            view=data["view"],
        )
        for item in data["repairs"]:
            project.repairs.append(
                RepairOperation(
                    item["id"],
                    SampleRegion(**item["region"]),
                    DetectionSettings(**item["detection"]),
                    tuple(DetectionSettings(**value) for value in item["channel_settings"]),
                    tuple(ClipInterval(**value) for value in item["accepted"]),
                    RepairSettings(**item["settings"]),
                    item["method"],
                )
            )
        calibration = data["calibration"]
        if calibration:
            rate = project.source.sample_rate

            def cached_section(item):
                entry = item["section"]
                return SectionAssignment(
                    entry["id"],
                    entry["name"],
                    SampleRegion(**entry["region"]),
                    entry["target_id"],
                    MatchSettings(**entry["settings"]),
                )

            project.calibration = MatchCalibration(
                calibration["config_key"],
                calibration["input_key"],
                parse_filter(calibration["whole"], rate) if calibration["whole"] else None,
                tuple(
                    SectionCurve(
                        cached_section(item),
                        parse_spectrum(item["source"]),
                        parse_filter(item["filter"], rate),
                    )
                    for item in calibration["sections"]
                ),
            )
        validate_project(project)
        return project
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid project JSON; current project has not been replaced.") from error


def replay_repairs(audio, rate, operations):
    output = audio.copy()
    repaired = []
    for operation in operations:
        result = repair_clipping(
            audio, rate, operation.report(audio), operation.accepted, operation.settings
        )
        if result.rejected:
            raise ValueError(
                "Saved repair no longer passes reconstruction checks; its recipe is retained."
            )
        for interval in result.repaired:
            output[interval.start : interval.end, interval.channel] = result.audio[
                interval.start : interval.end, interval.channel
            ]
        repaired.extend(result.repaired)
    return RepairResult(output, tuple(repaired), ())


@dataclass
class HydratedProject:
    project: Project
    source: tuple | None
    reference: tuple | None
    repair: RepairResult | None
    messages: list[str]


def hydrate_project(project):
    messages = []

    def read_asset(asset, label):
        if asset is None:
            return None
        status = asset.verify()
        if status != "ready":
            messages.append(
                f"{label} recording is {status}. Saved choices are retained; relink the original file."
            )
            return None
        try:
            audio, rate = sf.read(asset.path, always_2d=True, dtype="float64")
            return audio, rate, analyse(audio, rate), Path(asset.path).name, PeakIndex(audio), asset
        except (OSError, RuntimeError, ValueError) as error:
            messages.append(f"{label} recording could not be read: {error}. Choices retained.")
            return None

    source = read_asset(project.source, "Source")
    reference = read_asset(project.reference, "Reference")
    repair = None
    if source is not None and project.repairs:
        try:
            repair = replay_repairs(source[0], source[1], project.repairs)
        except ValueError as error:
            messages.append(str(error))
    return HydratedProject(project, source, reference, repair, messages)
