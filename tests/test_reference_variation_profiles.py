"""Versioned measured variation, old-target compatibility and unchanged matching."""

import json

import numpy as np
import pytest
import soundfile as sf

from omazone.engine import MatchSettings, analyse, design_match
from omazone.project import AudioReference, MatchCalibration, Project, load_project, save_project
from omazone.sections import capture_target, load_targets, save_targets
from omazone.waveform import SampleRegion


@pytest.fixture
def profile():
    rate = 16000
    audio = np.random.default_rng(101).normal(0, 0.1, (rate * 8, 2))
    return (
        audio,
        rate,
        capture_target(audio, rate, SampleRegion(rate, rate * 7), "Passage", "p", "Reference"),
    )


def test_new_profiles_keep_statistics_and_policy_without_audio(profile, tmp_path):
    _, _, target = profile
    path = tmp_path / "targets.json"
    save_targets(path, [target])
    data = json.loads(path.read_text())
    assert data["version"] == 2
    assert "audio" not in data["targets"][0]
    restored = load_targets(path)[0]
    assert restored.region == target.region
    assert restored.variation.frames == target.region.end - target.region.start
    np.testing.assert_array_equal(restored.variation.counts, target.variation.counts)
    np.testing.assert_array_equal(restored.variation.median_db, target.variation.median_db)
    assert data["targets"][0]["variation"]["policy"]["weighting"] == "equal-accepted-excerpts"


def test_legacy_mean_only_profiles_do_not_fabricate_a_range(profile, tmp_path):
    _, _, target = profile
    path = tmp_path / "targets.json"
    save_targets(path, [target])
    data = json.loads(path.read_text())
    data["version"] = 1
    del data["targets"][0]["variation"]
    path.write_text(json.dumps(data))
    restored = load_targets(path)[0]
    assert restored.variation is None
    np.testing.assert_array_equal(restored.spectrum.power, target.spectrum.power)
    save_targets(path, [restored])
    assert json.loads(path.read_text())["version"] == 1


def test_ordinary_matching_is_identical_with_or_without_distribution(profile, tmp_path):
    audio, rate, target = profile
    source = analyse(audio, rate)
    before = design_match(source, target.spectrum, rate)
    path = tmp_path / "targets.json"
    save_targets(path, [target])
    restored = load_targets(path)[0]
    after = design_match(source, restored.spectrum, rate)
    np.testing.assert_array_equal(after.coefficients, before.coefficients)
    np.testing.assert_array_equal(after.requested_db, before.requested_db)


def test_project_roundtrip_keeps_profiles_without_invalidating_calibration(profile, tmp_path):
    audio, rate, target = profile
    source = tmp_path / "mix.wav"
    sf.write(source, audio, rate, subtype="DOUBLE")
    project = Project(
        source=AudioReference.from_path(source),
        reference_target=target,
        targets={target.id: target},
        match_mode="whole",
        matching=MatchSettings(taps=129),
    )
    spec = design_match(analyse(audio, rate), target.spectrum, rate, project.matching)
    project.calibration = MatchCalibration(project.config_key(), project.input_key(), whole=spec)
    project.view["show_reference_variation"] = False
    path = tmp_path / "project.json"
    save_project(path, project)
    restored = load_project(path)
    assert not restored.needs_reanalysis
    assert restored.config_key() == project.config_key()
    assert restored.input_key() == project.input_key()
    assert restored.view["show_reference_variation"] is False
    np.testing.assert_array_equal(
        restored.reference_target.variation.counts, target.variation.counts
    )
    np.testing.assert_array_equal(
        restored.targets[target.id].variation.counts, target.variation.counts
    )
    np.testing.assert_array_equal(restored.calibration.whole.coefficients, spec.coefficients)
    data = json.loads(path.read_text())
    del data["reference_target"]["variation"]
    del data["targets"][0]["variation"]
    path.write_text(json.dumps(data))
    legacy = load_project(path)
    assert legacy.reference_target.variation is None and not legacy.needs_reanalysis


@pytest.mark.parametrize("change", ["policy", "passage", "version"])
def test_malformed_optional_data_is_rejected_not_dropped(profile, tmp_path, change):
    _, _, target = profile
    path = tmp_path / "targets.json"
    save_targets(path, [target])
    data = json.loads(path.read_text())
    if change == "policy":
        data["targets"][0]["variation"]["policy"]["normalisation"] = "unknown"
    elif change == "passage":
        data["targets"][0]["sample_rate"] = 48000
    else:
        data["version"] = 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_targets(path)
