"""Range-aware correction stays deterministic, bounded and distinct from its display."""

from dataclasses import asdict, replace

import numpy as np
import pytest
from scipy import signal

from omazone.engine import MatchSettings, analyse, design_match, render
from omazone.project import Project, digest_json, load_project, save_project, spectrum_dict
from omazone.range_match import design_range_match, design_target_match, range_signature
from omazone.reference_variation import analyse_variation, parse_variation, variation_dict
from omazone.sections import SectionAssignment, capture_target, render_sections
from omazone.waveform import SampleRegion

RATE = 16000


@pytest.fixture
def recording():
    audio = np.random.default_rng(100).normal(0, 0.1, (RATE * 4, 2))
    target = capture_target(audio, RATE, SampleRegion(0, len(audio)), "Reference", "r", "Generated")
    return audio, target


def settings(**kwargs):
    return MatchSettings(method="range-aware-v1", **kwargs)


def test_identical_reference_is_neutral_not_median_chasing(recording):
    audio, target = recording
    spec = design_range_match(audio, RATE, target.variation, settings())
    assert np.max(np.abs(spec.requested_db)) < 1e-10
    np.testing.assert_allclose(render(audio, spec), audio, atol=1e-12)


def test_ordinary_default_and_dispatch_remain_exactly_unchanged(recording):
    audio, target = recording
    expected = design_match(analyse(audio, RATE), target.spectrum, RATE)
    actual = design_target_match(audio, RATE, target, MatchSettings())
    np.testing.assert_array_equal(expected.coefficients, actual.coefficients)
    np.testing.assert_array_equal(expected.requested_db, actual.requested_db)
    with pytest.raises(ValueError, match="excerpt observations"):
        design_match(analyse(audio, RATE), target.spectrum, RATE, settings())


def test_broad_tonal_imbalance_gets_bounded_correction_and_rendered_improvement(recording):
    audio, target = recording
    dark = signal.sosfilt(signal.butter(1, 1000, fs=RATE, output="sos"), audio, axis=0)
    spec = design_range_match(
        dark, RATE, target.variation, settings(amount=1, max_boost_db=4, max_cut_db=3)
    )
    assert np.max(spec.requested_db) > 2
    assert np.min(spec.requested_db) < -1
    assert np.max(spec.requested_db) <= 4 and np.min(spec.requested_db) >= -3
    output = render(dark, spec)
    diagnostic = settings(amount=1, max_boost_db=18, max_cut_db=18)
    before_spec = design_range_match(dark, RATE, target.variation, diagnostic)
    next_spec = design_range_match(output, RATE, target.variation, diagnostic)
    assert np.mean(np.abs(next_spec.requested_db)) < np.mean(np.abs(before_spec.requested_db))
    assert output.shape == audio.shape and np.all(np.isfinite(output))


def test_already_in_range_material_gets_little_correction():
    block = np.random.default_rng(110).normal(0, 0.1, (RATE, 2))
    candidates = [
        signal.sosfilt(signal.butter(1, hz, fs=RATE, output="sos"), block, axis=0)
        for hz in (500, 1000, 2000, 4000)
    ]
    reference = np.concatenate([np.tile(item, (4, 1)) for item in candidates])
    variation = analyse_variation(reference, RATE)
    source = np.tile(candidates[1], (4, 1))
    spec = design_range_match(source, RATE, variation, settings(amount=1))
    assert np.max(np.abs(spec.requested_db)) < 0.05


def test_gain_scaling_and_excerpt_reordering_are_deterministic(recording):
    audio, target = recording
    spec = design_range_match(audio, RATE, target.variation, settings())
    for transformed in (audio * 0.2, audio * 3, audio.reshape(4, RATE, 2)[::-1].reshape(-1, 2)):
        actual = design_range_match(transformed, RATE, target.variation, settings())
        np.testing.assert_allclose(actual.requested_db, spec.requested_db, atol=1e-10)
    repeat = design_range_match(audio, RATE, target.variation, settings())
    np.testing.assert_array_equal(repeat.coefficients, spec.coefficients)


def test_amount_scales_limits_and_zero_amount_is_neutral(recording):
    audio, target = recording
    dark = signal.sosfilt(signal.butter(1, 1000, fs=RATE, output="sos"), audio, axis=0)
    full = design_range_match(dark, RATE, target.variation, settings(amount=1))
    part = design_range_match(dark, RATE, target.variation, settings(amount=0.25))
    zero = design_range_match(dark, RATE, target.variation, settings(amount=0))
    np.testing.assert_allclose(part.requested_db, full.requested_db * 0.25, atol=1e-12)
    np.testing.assert_array_equal(zero.requested_db, 0)
    np.testing.assert_allclose(render(dark, zero), dark, atol=1e-12)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"method": "future"},
        {"amount": float("nan")},
        {"amount": 2},
        {"taps": 128},
        {"taps": 129.5},
        {"smoothing_octaves": -1},
        {"max_cut_db": float("inf")},
    ],
)
def test_invalid_method_policy_and_settings_fail_explicitly(recording, kwargs):
    audio, target = recording
    config = replace(settings(), **kwargs)
    with pytest.raises(ValueError, match="Invalid range-aware"):
        design_range_match(audio, RATE, target.variation, config)


def test_empty_v2_reference_roundtrip_keeps_unavailable_status():
    variation = analyse_variation(np.zeros((RATE * 4, 1)), RATE)
    restored = parse_variation(variation_dict(variation))
    assert restored.observations_db.shape == (0, len(restored.valid))
    assert not restored.available


def test_common_domain_cancels_ultrasonic_normalisation_offsets():
    rate = 48000
    noise = np.random.default_rng(111).normal(0, 0.1, (rate * 4, 1))
    base = signal.sosfilt(signal.butter(8, 5000, fs=rate, output="sos"), noise, axis=0)
    t = np.arange(len(base)) / rate
    reference = base + 0.2 * np.sin(2 * np.pi * 18000 * t)[:, None]
    source = signal.resample_poly(base, 1, 3)
    with_highs = design_range_match(
        source, RATE, analyse_variation(reference, rate), settings(amount=1)
    )
    without_highs = design_range_match(
        source, RATE, analyse_variation(base, rate), settings(amount=1)
    )
    np.testing.assert_allclose(with_highs.requested_db, without_highs.requested_db, atol=0.03)
    assert np.max(np.abs(with_highs.requested_db)) < 0.2


@pytest.mark.parametrize("kind", ["short", "silent", "sparse"])
def test_unsupported_source_is_explained_not_fabricated(recording, kind):
    audio, target = recording
    if kind == "short":
        audio = audio[: RATE * 3]
    elif kind == "silent":
        audio = np.zeros_like(audio)
    else:
        audio = np.concatenate((audio[:RATE], np.zeros((RATE * 3, 2))))
    with pytest.raises(ValueError, match="four"):
        design_range_match(audio, RATE, target.variation, settings())


def test_near_empty_bands_are_not_boosted(recording):
    _, target = recording
    t = np.arange(RATE * 4) / RATE
    tone = 0.1 * np.sin(2 * np.pi * 1000 * t)[:, None]
    spec = design_range_match(tone, RATE, target.variation, settings(amount=1))
    assert np.max(spec.requested_db) <= 0  # Only the energetic tonal band can be cut.
    assert np.all(spec.requested_db[spec.frequency > 2000] == 0)


def test_opposite_polarity_stereo_does_not_cancel_during_matching():
    mono = np.random.default_rng(112).normal(0, 0.1, (RATE * 4, 1))
    reference = analyse_variation(mono, RATE)
    dark = signal.sosfilt(signal.butter(1, 1000, fs=RATE, output="sos"), mono, axis=0)
    stereo = np.column_stack((dark[:, 0], -dark[:, 0]))
    a = design_range_match(dark, RATE, reference, settings())
    b = design_range_match(stereo, RATE, reference, settings())
    np.testing.assert_allclose(a.coefficients, b.coefficients, atol=1e-12)
    rendered = render(stereo, b, block_size=257)
    np.testing.assert_allclose(rendered[:, 0], -rendered[:, 1], atol=1e-12)


def test_v2_observations_roundtrip_and_v1_stays_display_only(recording):
    audio, target = recording
    data = variation_dict(target.variation)
    assert data["kind"] == "reference-variation-v2"
    restored = parse_variation(data)
    np.testing.assert_array_equal(restored.observations_db, target.variation.observations_db)
    legacy = parse_variation(variation_dict(replace(target.variation, observations_db=None)))
    assert legacy.available and legacy.observations_db is None
    np.testing.assert_array_equal(legacy.counts, target.variation.counts)
    for variation in (None, legacy):
        with pytest.raises(ValueError, match="Reload"):
            design_range_match(audio, RATE, variation, settings())


@pytest.mark.parametrize("change", ["count", "quantile", "rows", "boolean", "nonfinite", "shape"])
def test_tampered_v2_observations_are_rejected(recording, change):
    _, target = recording
    data = variation_dict(target.variation)
    if change == "count":
        data["accepted_windows"] += 1
    elif change == "quantile":
        data["median_db"][5] += 0.01
    elif change == "rows":
        data["observations_db"][0][5] += 10
    elif change == "boolean":
        data["observations_db"][0][5] = False
    elif change == "nonfinite":
        data["observations_db"][0][5] = float("nan")
    else:
        data["observations_db"] = [item for row in data["observations_db"] for item in row]
    with pytest.raises(ValueError):
        parse_variation(data)


def test_legacy_calibration_key_is_preserved_but_range_data_affects_new_key(recording):
    _, target = recording
    project = Project(reference_target=target, match_mode="whole")
    old_settings = asdict(project.matching)
    del old_settings["method"]
    expected = digest_json(
        {"mode": "whole", "settings": old_settings, "target": spectrum_dict(target.spectrum)}
    )
    assert project.config_key() == expected
    ordinary = project.config_key()
    project.matching = settings()
    first_key = project.config_key()
    modified = target.variation.observations_db.copy()
    modified[0, 3] += 0.1
    project.reference_target = replace(
        target, variation=replace(target.variation, observations_db=modified)
    )
    assert project.config_key() != first_key
    project.matching = MatchSettings()
    assert project.config_key() == ordinary
    assert range_signature(target.variation) == range_signature(
        parse_variation(variation_dict(target.variation))
    )


def test_section_range_method_reuses_existing_transition_and_saved_curve_path(recording):
    audio, target = recording
    section = SectionAssignment("s", "Verse", SampleRegion(0, len(audio)), target.id, settings())
    result = render_sections(audio, RATE, [section], {target.id: target})
    replay = render_sections(
        audio, RATE, [section], {target.id: target}, learned_curves=result.curves, block_size=257
    )
    np.testing.assert_allclose(result.audio, replay.audio, atol=1e-12)
    np.testing.assert_allclose(result.audio, audio, atol=1e-12)


def test_whole_method_and_excerpt_data_survive_project_save(recording, tmp_path):
    _, target = recording
    project = Project(reference_target=target, matching=settings())
    path = tmp_path / "range.omazone.json"
    save_project(path, project)
    restored = load_project(path)
    assert restored.matching.method == "range-aware-v1"
    assert restored.config_key() == project.config_key()
    np.testing.assert_array_equal(
        restored.reference_target.variation.observations_db, target.variation.observations_db
    )
