import json
from itertools import pairwise

import numpy as np
import pytest
from scipy import signal

from omazone.engine import MatchSettings, analyse, render
from omazone.sections import (
    SectionAssignment,
    capture_target,
    load_targets,
    render_sections,
    save_targets,
    section_curve,
    transition_plan,
    validate_sections,
)
from omazone.waveform import SampleRegion


def fixture_audio():
    rate = 16000
    audio = np.random.default_rng(11).normal(0, 0.1, (rate * 2, 2))
    reference = signal.sosfilt(signal.butter(2, 1200, fs=rate, output="sos"), audio, axis=0)
    target = capture_target(
        reference, rate, SampleRegion(0, len(reference)), "Clean", "clean", "generated"
    )
    return rate, audio, {target.id: target}


def test_profiles_roundtrip_without_audio_and_validate_input(tmp_path):
    rate, audio, targets = fixture_audio()
    path = tmp_path / "targets.json"
    save_targets(path, targets.values())
    saved = json.loads(path.read_text())
    assert "audio" not in saved["targets"][0]
    loaded = load_targets(path)[0]
    assert loaded.name == "Clean"
    np.testing.assert_array_equal(loaded.spectrum.power, targets["clean"].spectrum.power)
    saved["targets"][0]["frequency"][1] = -1
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="frequency"):
        load_targets(path)
    with pytest.raises(ValueError, match="0.1"):
        capture_target(audio, rate, SampleRegion(0, 3), "Tiny", "t", "test")
    with pytest.raises(ValueError, match="silent"):
        capture_target(np.zeros_like(audio), rate, SampleRegion(0, rate), "Silence", "s", "test")


def test_whole_song_assignment_matches_existing_renderer():
    rate, audio, targets = fixture_audio()
    section = SectionAssignment("a", "All", SampleRegion(0, len(audio)), "clean")
    curve = section_curve(audio, rate, section, targets)
    result = render_sections(audio, rate, [section], targets)
    np.testing.assert_allclose(result.audio, render(audio, curve.filter), atol=1e-12)
    assert result.transitions == ()


def test_context_matches_full_convolution_and_dry_gaps_remain_exact():
    rate, audio, targets = fixture_audio()
    section = SectionAssignment("a", "Middle", SampleRegion(8000, 20000), "clean")
    curve = section_curve(audio, rate, section, targets)
    result = render_sections(audio, rate, [section], targets, transition_ms=0, block_size=37)
    full = render(audio, curve.filter)
    np.testing.assert_allclose(result.audio[8000:20000], full[8000:20000], atol=1e-12)
    np.testing.assert_array_equal(result.audio[:8000], audio[:8000])
    np.testing.assert_array_equal(result.audio[20000:], audio[20000:])


def test_adjacent_sections_have_complementary_aligned_transitions():
    rate, audio, targets = fixture_audio()
    a = SectionAssignment("a", "A", SampleRegion(0, 16000), "clean")
    b = SectionAssignment(
        "b", "B", SampleRegion(16000, len(audio)), "clean", MatchSettings(amount=0.9)
    )
    result = render_sections(audio, rate, [a, b], targets, transition_ms=100, block_size=511)
    other = render_sections(audio, rate, [a, b], targets, transition_ms=100, block_size=4096)
    np.testing.assert_allclose(result.audio, other.audio, atol=1e-12)
    assert result.audio.shape == audio.shape
    transition = result.transitions[0]
    assert (transition.start, transition.end) == (15200, 16800)
    outputs = [render(audio, item.filter) for item in result.curves]
    weight = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, 1600)))[:, None]
    expected = outputs[0][15200:16800] * (1 - weight) + outputs[1][15200:16800] * weight
    np.testing.assert_allclose(result.audio[15200:16800], expected, atol=1e-12)


def test_identity_sections_and_dry_transitions_preserve_stereo():
    rate, audio, targets = fixture_audio()
    audio[:, 1] = -0.4 * audio[:, 0]
    sections = [
        SectionAssignment("a", "A", SampleRegion(4000, 10000), "clean", MatchSettings(amount=0)),
        SectionAssignment("b", "B", SampleRegion(18000, 22000), "clean", MatchSettings(amount=0)),
    ]
    result = render_sections(audio, rate, sections, targets, transition_ms=500)
    np.testing.assert_allclose(result.audio, audio, atol=1e-12)
    filtered_sections = [SectionAssignment(s.id, s.name, s.region, s.target_id) for s in sections]
    filtered = render_sections(audio, rate, filtered_sections, targets, transition_ms=500)
    np.testing.assert_allclose(filtered.audio[:, 1], -0.4 * filtered.audio[:, 0], atol=1e-12)


def test_transition_windows_do_not_collide_and_overlaps_are_rejected():
    rate, audio, targets = fixture_audio()
    a = SectionAssignment("a", "A", SampleRegion(1000, 3000), "clean")
    b = SectionAssignment("b", "B", SampleRegion(3001, 5001), "clean")
    plan = transition_plan([a, b], len(audio), rate, 5000)
    assert all(left.end <= right.start for left, right in pairwise(plan))
    overlap = SectionAssignment("b", "Overlap", SampleRegion(2000, 6000), "clean")
    with pytest.raises(ValueError, match="overlap"):
        validate_sections([a, overlap], targets, len(audio), rate)


def test_target_at_different_sample_rate_and_section_analysis_is_local():
    rate, audio, targets = fixture_audio()
    reference = signal.resample_poly(audio, 3, 2)
    target = capture_target(
        reference, 24000, SampleRegion(0, len(reference)), "Other rate", "other", "test"
    )
    targets[target.id] = target
    section = SectionAssignment("a", "First", SampleRegion(0, 8000), "other")
    curve = section_curve(audio, rate, section, targets)
    np.testing.assert_array_equal(curve.source.power, analyse(audio[:8000], rate).power)
    assert np.all(np.isfinite(render_sections(audio, rate, [section], targets).audio))


def test_identical_filters_do_not_gain_boost_at_boundaries(monkeypatch):
    import omazone.sections as section_engine

    rate, audio, targets = fixture_audio()
    a = SectionAssignment("a", "A", SampleRegion(0, 16000), "clean")
    b = SectionAssignment("b", "B", SampleRegion(16000, len(audio)), "clean")
    spec = section_curve(audio, rate, a, targets).filter
    audio[16000] += 0.9  # Test filter context on both sides of a boundary impulse.
    monkeypatch.setattr(
        section_engine,
        "section_curve",
        lambda audio, rate, section, targets: section_engine.SectionCurve(
            section, analyse(audio, rate), spec
        ),
    )
    result = render_sections(audio, rate, [a, b], targets, transition_ms=150)
    np.testing.assert_allclose(result.audio, render(audio, spec), atol=1e-12)


def test_unassigned_audio_outside_visible_windows_is_bitwise_unchanged():
    rate, audio, targets = fixture_audio()
    section = SectionAssignment("a", "Middle", SampleRegion(8000, 20000), "clean")
    result = render_sections(audio, rate, [section], targets, transition_ms=100)
    modified = np.zeros(len(audio), dtype=bool)
    modified[8000:20000] = True
    for window in result.transitions:
        modified[window.start : window.end] = True
    np.testing.assert_array_equal(result.audio[~modified], audio[~modified])
