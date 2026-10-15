"""Raw stage-boundary metering, cache identity and scope without GUI or audio."""

import gc
import weakref
from dataclasses import replace

import numpy as np
import pytest

from omazone.comparison import SPECS, ComparisonState, Side
from omazone.loudness import integrated_lufs
from omazone.project import STAGES
from omazone.stage_measurements import MeasurementCache, request_for
from omazone.waveform import SampleRegion
from omazone.workflow_status import RenderState, StageStatus


def statuses(**changes):
    result = {stage: StageStatus(RenderState.READY, "Ready", "Compare") for stage in STAGES}
    for stage, state in changes.items():
        result[stage] = StageStatus(state, "Render to update output", "Render")
    return result


def tone(rate=16000, seconds=1, amplitude=0.25, channels=1):
    audio = amplitude * np.sin(2 * np.pi * 1000 * np.arange(int(rate * seconds)) / rate)
    return np.repeat(audio[:, None], channels, axis=1)


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_gain_only_moves_peak_and_loudness_by_same_amount(rate, channels):
    before = tone(rate=rate, channels=channels)
    after = before * 10 ** (-6 / 20)
    cache = MeasurementCache()
    first = cache.measure(before, rate, 0, len(before))
    second = cache.measure(after, rate, 0, len(after))
    assert first.peak_db == pytest.approx(20 * np.log10(np.max(np.abs(before))))
    assert second.peak_db == pytest.approx(first.peak_db - 6)
    assert second.lufs == pytest.approx(first.lufs - 6, abs=0.001)
    assert first.lufs == pytest.approx(integrated_lufs(before, rate))


def test_selected_passage_is_not_whole_recording_or_surrounding_samples():
    audio = np.concatenate((tone(amplitude=0.8), tone(amplitude=0.05)))
    cache = MeasurementCache()
    full = cache.measure(audio, 16000, 0, len(audio))
    selected = cache.measure(audio, 16000, 16000, 32000)
    assert selected.peak_db == pytest.approx(20 * np.log10(0.05))
    assert selected.lufs == pytest.approx(integrated_lufs(audio[16000:], 16000))
    assert full.peak_db > selected.peak_db + 20
    assert full.lufs > selected.lufs + 10


@pytest.mark.parametrize(
    "audio,note",
    [
        (np.zeros((16000, 1)), "Silence"),
        (tone(seconds=0.2), "Under 400 ms"),
        (tone(amplitude=1e-8), "Below gate"),
    ],
)
def test_undefined_loudness_is_explained(audio, note):
    reading = MeasurementCache().measure(audio, 16000, 0, len(audio))
    assert reading.lufs is None and reading.loudness_note == note
    if note == "Silence":
        assert reading.peak_db == float("-inf")


def test_cache_reuses_unchanged_scope_and_renderer_lufs_without_retaining_audio(monkeypatch):
    audio = tone()
    cache = MeasurementCache(limit=2)
    calls = []

    def measured(segment, rate):
        calls.append((len(segment), rate))
        return -20.0

    monkeypatch.setattr("omazone.stage_measurements.integrated_lufs", measured)
    first = cache.measure(audio, 16000, 0, len(audio), {id(audio): -18.0})
    assert first.lufs == -18 and not calls
    assert cache.measure(audio, 16000, 0, len(audio)) is first
    selected = cache.measure(audio, 16000, 0, 8000, {id(audio): -18.0})
    assert selected.lufs == -20 and calls == [(8000, 16000)]
    cache.measure(audio, 48000, 0, len(audio))
    assert len(cache.entries) == 2
    reference = weakref.ref(audio)
    del audio
    gc.collect()
    assert reference() is None


def test_cache_requires_object_identity_even_if_numeric_id_key_collides():
    cache = MeasurementCache()
    first, second = tone(), tone(amplitude=0.5)
    wrong = cache.measure(first, 16000, 0, len(first))
    cache.entries[(id(second), 16000, 0, len(second))] = (weakref.ref(first), wrong)
    assert cache.measure(second, 16000, 0, len(second)).peak_db != wrong.peak_db


@pytest.mark.parametrize("start,end", [(0, 0), (-1, 100), (0, 16001), (200, 100)])
def test_invalid_scope_is_not_replaced_with_whole_recording(start, end):
    with pytest.raises(ValueError):
        MeasurementCache().measure(tone(), 16000, start, end)


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_nonfinite_samples_cannot_produce_a_normal_reading(value):
    audio = tone()
    audio[20] = value
    with pytest.raises(ValueError, match="non-finite"):
        MeasurementCache().measure(audio, 16000, 0, len(audio))


def test_nonfinite_cached_loudness_cannot_produce_a_normal_reading():
    audio = tone()
    with pytest.raises(ValueError, match="Loudness measurement is non-finite"):
        MeasurementCache().measure(audio, 16000, 0, len(audio), {id(audio): float("nan")})


@pytest.mark.parametrize("spec", [spec for spec in SPECS if spec.stage and spec.key != "band-solo"])
def test_request_uses_exact_raw_stage_pair_not_playback_or_final_chain(spec):
    before, after = tone(), tone(amplitude=0.5)
    preview = (before * 0.1, after * 0.1)
    state = ComparisonState(spec.key, Side.BEFORE, before, after, preview, 16000)
    request = request_for(state, statuses(), SampleRegion(4000, 14000))
    assert request.before is before and request.after is after
    assert (request.start, request.end) == (4000, 14000)
    assert "Selected passage 0.250-0.875 s" == request.scope
    assert (
        request.key
        == request_for(replace(state, side=Side.AFTER), statuses(), SampleRegion(4000, 14000)).key
    )
    assert "audition matching excluded" in request.status


def test_stale_output_is_removed_but_current_input_is_retained():
    state = ComparisonState("eq", Side.AFTER, tone(), tone(), None, 16000)
    request = request_for(state, statuses(eq=RenderState.NEEDS_RENDER))
    assert request.before is state.before and request.after is None
    assert "Render" in request.status


def test_upstream_stale_context_is_not_measured_as_current_input():
    state = ComparisonState("dynamics", Side.BEFORE, tone(), None, None, 16000)
    request = request_for(state, statuses(eq=RenderState.NEEDS_RENDER))
    assert request.before is None and request.after is None
    assert "input unavailable" in request.status


def test_skipped_stage_and_audition_only_repair_preview_are_explicit():
    before = tone()
    state = ComparisonState("eq", Side.BEFORE, before, before, None, 16000, True)
    request = request_for(state, statuses(eq=RenderState.SKIPPED))
    assert request.before is before and request.after is before
    assert "Skipped" in request.status
    state = replace(state, key="repair", after=tone(amplitude=0.5))
    request = request_for(state, statuses(repair=RenderState.SKIPPED))
    assert request.after is state.after and "repair preview" in request.status


def test_solo_does_not_masquerade_as_full_stage_measurements():
    state = ComparisonState("band-solo", Side.BEFORE, tone(), None, None, 16000)
    request = request_for(state, statuses())
    assert request.before is None and request.after is None
    assert "audition-only" in request.status
