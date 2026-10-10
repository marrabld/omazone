import json
import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from omazone.clipping import DetectionSettings, detect_clipping
from omazone.declipping import RepairSettings, repair_clipping
from omazone.engine import MatchSettings, analyse, design_match, render
from omazone.project import (
    AudioReference,
    MatchCalibration,
    NamedRegion,
    Project,
    RepairOperation,
    hydrate_project,
    load_project,
    replay_repairs,
    save_project,
)
from omazone.sections import (
    SectionAssignment,
    capture_target,
    load_targets,
    render_sections,
    save_targets,
)
from omazone.waveform import SampleRegion


def session(tmp_path):
    rate = 16000
    audio = np.random.default_rng(81).normal(0, 0.1, (rate * 2, 2))
    reference = signal.resample_poly(audio, 3, 2)
    source_path, reference_path = tmp_path / "mix.wav", tmp_path / "reference.wav"
    sf.write(source_path, audio, rate, subtype="DOUBLE")
    sf.write(reference_path, reference, 24000, subtype="DOUBLE")
    target = capture_target(
        reference, 24000, SampleRegion(0, len(reference)), "Clean", "clean", "generated"
    )
    project = Project(
        source=AudioReference.from_path(source_path),
        reference=AudioReference.from_path(reference_path),
        reference_target=target,
    )
    project.targets[target.id] = target
    sections = [
        SectionAssignment("a", "Intro", SampleRegion(0, rate), "clean"),
        SectionAssignment(
            "b", "Outro", SampleRegion(rate, len(audio)), "clean", MatchSettings(amount=0.75)
        ),
    ]
    project.import_sections(sections)
    project.regions.append(NamedRegion("guitar", "Harsh passage", SampleRegion(4000, 8000)))
    project.match_mode = "sections"
    result = render_sections(audio, rate, sections, project.targets)
    project.calibration = MatchCalibration(
        project.config_key(), project.input_key(), sections=result.curves
    )
    project.stages["eq"].parameters = {
        "bands": [{"region_id": "guitar", "frequency": 2400, "gain_db": -2, "q": 1}]
    }
    project.stages["dynamics"].parameters = {"threshold_db": -18, "ratio": 2}
    project.stages["output"].parameters = {"ceiling_db": -1}
    project.view = {
        "selection": [4000, 8000],
        "loop_region": [4000, 8000],
        "position": 4200,
        "loop_enabled": True,
        "zoom": [0.2, 0.6],
        "viewer_mode": "both",
    }
    return project, audio


def test_complete_recipe_roundtrip_and_locked_section_render(tmp_path):
    project, audio = session(tmp_path)
    file = tmp_path / "session.omazone.json"
    save_project(file, project)
    data = json.loads(file.read_text())
    assert data["source"]["path"] == "mix.wav"
    assert "audio" not in data
    loaded = load_project(file)
    assert loaded.id == project.id
    assert loaded.regions == project.regions
    assert loaded.sections == project.sections
    assert loaded.stages["eq"].parameters == project.stages["eq"].parameters
    assert loaded.stages["dynamics"].parameters == project.stages["dynamics"].parameters
    assert loaded.view == project.view
    assert loaded.can_render_saved_match and not loaded.needs_reanalysis
    before = render_sections(audio, 16000, project.sections, project.targets).audio
    after = render_sections(
        audio, 16000, loaded.sections, loaded.targets, learned_curves=loaded.calibration.sections
    ).audio
    np.testing.assert_allclose(after, before, atol=1e-12)
    assert loaded.needs_render  # Rendered audio itself was not stored.


def test_missing_changed_and_incompatible_source_retains_choices(tmp_path):
    project, audio = session(tmp_path)
    old_path = tmp_path / "mix.wav"
    relocated = tmp_path / "relocated.wav"
    old_path.rename(relocated)
    loaded = hydrate_project(project)
    assert loaded.source is None and "missing" in loaded.messages[0]
    assert len(project.sections) == 2 and project.stages["eq"].parameters
    wrong = tmp_path / "wrong.wav"
    sf.write(wrong, audio * 0.5, 16000, subtype="DOUBLE")
    old_asset = project.source
    with pytest.raises(ValueError, match="changed"):
        project.relink("source", wrong)
    assert project.source is old_asset
    sf.write(wrong, audio, 24000, subtype="DOUBLE")
    with pytest.raises(ValueError, match="incompatible"):
        project.relink("source", wrong)
    project.relink("source", relocated)
    assert project.source.verify() == "ready"
    assert not project.needs_reanalysis
    np.testing.assert_array_equal(hydrate_project(project).source[0], audio)


def test_earlier_edits_preserve_calibration_and_independent_names_do_not_relearn(tmp_path):
    project, _ = session(tmp_path)
    calibration = project.calibration
    project.stages["repair"].bypassed = True
    assert project.needs_reanalysis
    assert project.calibration is calibration and project.can_render_saved_match
    project.stages["repair"].bypassed = False
    renamed = [replace(item, name=item.name + " renamed") for item in project.sections]
    project.import_sections(renamed)
    assert not project.needs_reanalysis
    project.import_sections([])
    assert project.regions  # Annotation is retained when an effect assignment is removed.
    file = tmp_path / "stale.omazone.json"
    save_project(file, project)
    restored = load_project(file)
    assert len(restored.calibration.sections) == 2
    assert not restored.sections


def test_whole_filter_survives_reference_unavailability_without_double_application(tmp_path):
    project, audio = session(tmp_path)
    project.match_mode = "whole"
    spec = design_match(analyse(audio, 16000), project.reference_target.spectrum, 16000)
    project.calibration = MatchCalibration(project.config_key(), project.input_key(), whole=spec)
    file = tmp_path / "whole.omazone.json"
    save_project(file, project)
    (tmp_path / "reference.wav").unlink()
    loaded = load_project(file)
    hydrated = hydrate_project(loaded)
    assert hydrated.source is not None and hydrated.reference is None
    assert loaded.reference_target is not None
    np.testing.assert_allclose(
        render(hydrated.source[0], loaded.calibration.whole), render(audio, spec), atol=1e-12
    )


def test_multiple_repairs_replay_from_original_with_exact_mask_preservation(tmp_path):
    rate = 48000
    t = np.arange(12000) / rate
    original = np.clip(0.9 * np.sin(2 * np.pi * 440 * t), -0.45, 0.6)[:, None]
    path = tmp_path / "guitar.wav"
    sf.write(path, original, rate, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(path))
    for index, bounds in enumerate((SampleRegion(0, 6000), SampleRegion(6000, 12000))):
        report = detect_clipping(original, bounds, DetectionSettings(0.6, -0.45))
        result = repair_clipping(original, rate, report, report.candidates)
        project.repairs.append(
            RepairOperation(
                str(index),
                bounds,
                report.settings,
                report.channel_settings,
                result.repaired,
                RepairSettings(),
            )
        )
    expected = replay_repairs(original, rate, project.repairs)
    file = tmp_path / "repair.omazone.json"
    save_project(file, project)
    loaded = load_project(file)
    replayed = hydrate_project(loaded).repair
    np.testing.assert_array_equal(replayed.audio, expected.audio)
    mask = np.zeros(original.shape, dtype=bool)
    for item in replayed.repaired:
        mask[item.start : item.end, item.channel] = True
    np.testing.assert_array_equal(replayed.audio[~mask], original[~mask])
    project.stages["repair"].bypassed = True
    save_project(file, project)
    assert load_project(file).repairs == project.repairs


def test_import_existing_target_profiles_and_atomic_failure(tmp_path):
    project, _ = session(tmp_path)
    targets_file = tmp_path / "targets.json"
    save_targets(targets_file, project.targets.values())
    imported = load_targets(targets_file)
    project.targets = {item.id: item for item in imported}
    file = tmp_path / "session.omazone.json"
    save_project(file, project)
    before = file.read_bytes()
    project.matching = MatchSettings(amount=float("nan"))
    with pytest.raises(ValueError):
        save_project(file, project)
    assert file.read_bytes() == before
    data = json.loads(before)
    data["version"] = 99
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="version"):
        load_project(file)


def test_a_saved_project_reopens_from_any_working_directory(tmp_path, monkeypatch):
    """The stored path is relative to the project, not to wherever the app runs.

    Every save/reopen test used to pass by coincidence because the suite runs
    from the directory the relative path happens to resolve against.
    """
    audio_dir = tmp_path / "Audio"
    project_dir = tmp_path / "Sessions"
    # A different depth, so "../Audio" cannot reach the recording by accident.
    elsewhere = tmp_path / "elsewhere" / "deeper"
    for directory in (audio_dir, project_dir, elsewhere):
        directory.mkdir(parents=True)
    project, audio = session(audio_dir)
    path = project_dir / "session.omazone.json"
    save_project(path, project)
    assert json.loads(path.read_text())["source"]["path"] == os.path.relpath(
        str((audio_dir / "mix.wav").resolve()), str(project_dir.resolve())
    )

    monkeypatch.chdir(elsewhere)
    reloaded = load_project(path)
    assert Path(reloaded.source.path).is_absolute()
    hydrated = hydrate_project(reloaded)
    assert hydrated.source is not None
    np.testing.assert_array_equal(hydrated.source[0], audio)
    assert hydrated.messages == []


def test_a_recording_with_no_relative_path_still_saves(tmp_path, monkeypatch):
    """Windows has no relative path across drives, so saving must not simply fail."""

    def no_relative_path(path, start):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", no_relative_path)
    project, _ = session(tmp_path)
    file = tmp_path / "session.omazone.json"
    save_project(file, project)
    stored = json.loads(file.read_text())["source"]["path"]
    assert stored == project.source.path
    assert hydrate_project(load_project(file)).source is not None
