"""Measured, bounded targeting proposals change no audio or processing recipe."""

from dataclasses import replace

import numpy as np
import pytest

from omazone.delivery import DeliverySettings, measure, measure_converted
from omazone.delivery_measurements import DeliveryMeasurements
from omazone.limiter import render_limiter
from omazone.loudness_target import (
    MAX_EVALUATIONS,
    TargetSettings,
    propose_target,
    settings_from_parameters,
    target_parameters,
)
from omazone.output_gain import OutputGainSettings, apply_output_gain

RATE = 16000


def music(rate=RATE):
    audio = np.random.default_rng(102).normal(0, 0.07, (rate * 2, 2))
    audio[:: rate // 10] += [0.7, -0.4]
    return audio


def test_attainable_gain_only_target_and_actual_render_agree():
    audio = music()
    original = audio.copy()
    request = TargetSettings(-18, -1)
    proposal = propose_target(audio, RATE, request)
    assert proposal.converged and proposal.limiter is None
    assert abs(proposal.measured.lufs - request.target_lufs) <= 0.1
    assert proposal.measured.true_peak_db <= request.ceiling_db
    rendered = apply_output_gain(audio, OutputGainSettings(proposal.gain_db))
    actual = measure_converted(rendered, RATE, RATE)
    assert actual.lufs == pytest.approx(proposal.measured.lufs, abs=1e-10)
    assert actual.true_peak_db == pytest.approx(proposal.measured.true_peak_db, abs=1e-10)
    np.testing.assert_array_equal(audio, original)


def test_peak_safe_gain_reports_unreached_target_without_enabling_limiter():
    request = TargetSettings(-10, -1)
    proposal = propose_target(music(), RATE, request)
    assert not proposal.converged and proposal.limiter is None
    assert proposal.measured.lufs < request.target_lufs - 0.1
    assert proposal.measured.true_peak_db <= request.ceiling_db
    assert "without limiting" in proposal.reason
    assert proposal.request == request


def test_explicit_limiting_can_reach_target_with_measured_reduction_and_repeatability():
    audio = music()
    request = TargetSettings(-10, -1, "limiter")
    proposal = propose_target(audio, RATE, request)
    repeated = propose_target(audio, RATE, request)
    assert proposal == repeated
    assert proposal.converged and proposal.reduction_db > 3
    assert 1 < proposal.evaluations <= MAX_EVALUATIONS
    assert proposal.limiter.ceiling_db <= request.ceiling_db
    rendered = render_limiter(
        apply_output_gain(audio, OutputGainSettings(proposal.gain_db)), RATE, proposal.limiter
    )
    actual = measure_converted(rendered.audio, RATE, RATE)
    assert actual.lufs == pytest.approx(proposal.measured.lufs, abs=1e-10)
    assert actual.true_peak_db <= -1


@pytest.mark.parametrize("mode", ["gain-only", "limiter"])
def test_proposal_measures_after_selected_delivery_conversion_and_real_recipe_agrees(mode):
    rate = 48000
    audio = music(rate)
    delivery = DeliverySettings(sample_rate=44100)
    request = TargetSettings(-14, -1, mode)
    proposal = propose_target(audio, rate, request, delivery)
    assert proposal.measured.rate == 44100 and proposal.measured.frames == 88200
    rendered = apply_output_gain(audio, OutputGainSettings(proposal.gain_db))
    if proposal.limiter is not None:
        rendered = render_limiter(rendered, rate, proposal.limiter).audio
    actual = measure_converted(rendered, rate, 44100)
    assert actual.lufs == pytest.approx(proposal.measured.lufs, abs=1e-9)
    assert actual.true_peak_db == pytest.approx(proposal.measured.true_peak_db, abs=1e-9)
    assert actual.true_peak_db <= -1 + 0.01


def test_gain_bounds_are_reported_not_silently_substituted_for_request():
    audio = music() * 0.02
    request = TargetSettings(-8, -1)
    proposal = propose_target(audio, RATE, request)
    assert proposal.gain_db == 18
    assert not proposal.converged and proposal.request.target_lufs == -8
    assert "stopped the search" in proposal.reason


@pytest.mark.parametrize("kind", ["short", "silence", "below-gate"])
def test_undefined_loudness_cannot_generate_arbitrary_gain(kind, monkeypatch):
    audio = music()
    if kind == "short":
        audio = audio[: RATE // 5]
    elif kind == "silence":
        audio[:] = 0
    else:
        audio *= 1e-6
    monkeypatch.setattr(
        "omazone.loudness_target.evaluate_candidate",
        lambda *args: pytest.fail("Undefined baseline generated a gain candidate"),
    )
    with pytest.raises(ValueError, match="400 ms"):
        propose_target(audio, RATE)


def test_impossible_safe_gain_raises_instead_of_offering_an_unsafe_apply():
    with pytest.raises(ValueError, match="No measured peak-safe"):
        propose_target(music() * 1e4, RATE, TargetSettings(-30, -24))


def test_integer_dither_headroom_is_included_in_peak_safe_proposal():
    delivery = DeliverySettings(format="pcm_24")
    proposal = propose_target(music(), RATE, TargetSettings(-5, 0), delivery)
    assert not proposal.converged
    rendered = apply_output_gain(music(), OutputGainSettings(proposal.gain_db))
    assert not measure(rendered, delivery).clipped


def test_file_edge_sample_peak_cannot_hide_behind_true_peak_meter_edge_trimming():
    audio = music() * 0.05
    audio[0] = [1.0, -1.0]
    request = TargetSettings(-18, -1)
    proposal = propose_target(audio, RATE, request)
    assert proposal.measured.peak <= 10 ** (request.ceiling_db / 20)
    assert not proposal.converged


def test_nonconvergence_has_a_fixed_evaluation_bound_and_no_false_success(monkeypatch):
    audio = music()
    baseline = measure_converted(audio, RATE, RATE)
    calls = []

    def fixed(audio, rate, delivery, gain, limiter, cancelled):
        calls.append(gain)
        return DeliveryMeasurements(rate, len(audio), 0.5, -2.0, -18.2), 0.0

    monkeypatch.setattr("omazone.loudness_target.evaluate_candidate", fixed)
    proposal = propose_target(audio, RATE, TargetSettings(-18, -1, "limiter"), baseline=baseline)
    assert len(calls) == proposal.evaluations == MAX_EVALUATIONS
    assert not proposal.converged and "Bounded search" in proposal.reason
    assert len(set(calls)) == MAX_EVALUATIONS


def test_converted_peak_overshoot_adjusts_the_proposed_existing_limiter_not_hidden_gain(
    monkeypatch,
):
    audio = music()
    baseline = measure_converted(audio, RATE, RATE)
    ceilings = []

    def overshoot(audio, rate, delivery, gain, limiter, cancelled):
        ceilings.append(limiter.ceiling_db)
        peak = limiter.ceiling_db + 1.1
        return DeliveryMeasurements(rate, len(audio), 10 ** (peak / 20), peak, -14.0), 2.0

    monkeypatch.setattr("omazone.loudness_target.evaluate_candidate", overshoot)
    proposal = propose_target(audio, RATE, TargetSettings(-14, -1, "limiter"), baseline=baseline)
    assert proposal.converged and len(ceilings) == 2
    assert proposal.limiter.ceiling_db < -2
    assert proposal.measured.true_peak_db <= -1


def test_cancelled_targeting_and_limiter_do_not_mutate_source():
    audio = music()
    original = audio.copy()
    with pytest.raises(RuntimeError, match="cancelled"):
        propose_target(audio, RATE, cancelled=lambda: True)
    with pytest.raises(RuntimeError, match="cancelled"):
        render_limiter(
            audio,
            RATE,
            __import__("omazone.limiter", fromlist=["LimiterSettings"]).LimiterSettings(),
            cancelled=lambda: True,
        )
    np.testing.assert_array_equal(audio, original)


@pytest.mark.parametrize(
    "changes",
    [
        {"target_lufs": True},
        {"target_lufs": float("nan")},
        {"target_lufs": -80},
        {"ceiling_db": 1},
        {"ceiling_db": -25},
        {"ceiling_db": float("inf")},
        {"mode": "automatic"},
    ],
)
def test_invalid_requests_fail_without_defaulting(changes):
    with pytest.raises(ValueError):
        target_parameters(replace(TargetSettings(), **changes))


def test_versioned_request_roundtrip_and_legacy_defaults():
    target = TargetSettings(-16.25, -1.25, "limiter")
    assert settings_from_parameters(target_parameters(target)) == target
    assert settings_from_parameters(None) == TargetSettings()
    with pytest.raises(ValueError):
        settings_from_parameters({"kind": "future"})
