"""One comparison model for pair, side, availability, labels, and arrays."""

import numpy as np
import pytest

from omazone.comparison import (
    SPECS,
    Side,
    StagePair,
    resolve,
    stage_for,
    upstream_prefix,
)
from omazone.engine import audition_pair
from omazone.project import STAGES

ORIGINAL = np.zeros((64, 2))
REPAIRED = np.ones((64, 2))
MATCHED = np.full((64, 2), 2.0)
EQUALIZED = np.full((64, 2), 3.0)
COMPRESSED = np.full((64, 2), 4.0)
FINAL = np.full((64, 2), 5.0)


def stage_arrays():
    return {
        "repair": (ORIGINAL, REPAIRED),
        "match": (REPAIRED, MATCHED),
        "eq": (MATCHED, EQUALIZED),
        "dynamics": (EQUALIZED, COMPRESSED),
        "output": (COMPRESSED, FINAL),
    }


def rendered_pairs(**stages):
    """Stage pairs carrying both arrays only for the stages that were rendered."""
    pairs = {}
    for stage, (before, after) in stage_arrays().items():
        present = stage in stages
        pairs[stage] = StagePair(
            before if present else None,
            after if present else None,
            audition_pair(before, after) if present else None,
        )
    return pairs


def test_every_comparison_names_a_stage_and_the_original_names_none():
    assert [spec.key for spec in SPECS] == [
        "mastering",
        "repair",
        "original",
        "eq",
        "dynamics",
        "output-gain",
    ]
    assert stage_for("mastering") == "match"
    assert stage_for("output-gain") == "output"
    assert stage_for("original") is None
    assert {spec.stage for spec in SPECS if spec.stage} <= set(STAGES)


def test_unknown_comparison_key_is_rejected():
    with pytest.raises(KeyError):
        resolve("sidechain", {})


def test_original_only_is_not_a_before_after_pair():
    state = resolve("original", rendered_pairs(eq=True), original=ORIGINAL, rate=48000)
    assert state.side is Side.ORIGINAL
    assert state.after is None
    assert not state.available
    assert state.side_label == "original"
    # Playing original-only still reads the original; there is simply nothing to
    # switch to, so the transport gets an identity pair of the recording.
    np.testing.assert_allclose(state.playback_arrays[0], ORIGINAL, atol=1e-6)
    np.testing.assert_allclose(state.playback_arrays[0], state.playback_arrays[1])


@pytest.mark.parametrize(
    ("key", "stage", "before", "after"),
    (
        ("repair", "repair", ORIGINAL, REPAIRED),
        ("mastering", "match", REPAIRED, MATCHED),
        ("eq", "eq", MATCHED, EQUALIZED),
        ("dynamics", "dynamics", EQUALIZED, COMPRESSED),
        ("output-gain", "output", COMPRESSED, FINAL),
    ),
)
def test_each_stage_compares_exact_chain_prefixes(key, stage, before, after):
    state = resolve(key, rendered_pairs(**{stage: True}), original=ORIGINAL, rate=48000)
    assert state.stage == stage
    assert state.available
    np.testing.assert_array_equal(state.before, before)
    np.testing.assert_array_equal(state.after, after)
    assert state.rate == 48000
    assert not np.array_equal(state.before, state.after)


def test_unrendered_stage_keeps_its_input_and_reports_no_after():
    pairs = rendered_pairs(match=True)
    state = resolve("eq", pairs, original=ORIGINAL, rate=48000, reason="Render Manual EQ.")
    assert not state.available
    assert state.after is None
    assert state.reason == "Render Manual EQ."
    np.testing.assert_array_equal(state.before, MATCHED)
    # With no After the transport falls back to the stage input it does have.
    np.testing.assert_allclose(state.playback_arrays[0], state.playback_arrays[1])


def test_downstream_stage_input_falls_back_through_a_bypassed_stage():
    pairs = rendered_pairs(repair=True, match=True)
    state = resolve("dynamics", pairs, original=ORIGINAL, rate=48000)
    # Manual EQ and Output were never rendered, so the chain input is matching output.
    np.testing.assert_array_equal(state.before, MATCHED)
    assert not state.available


def test_upstream_prefix_prefers_the_latest_rendered_stage():
    pairs = rendered_pairs(repair=True, match=True, eq=True)
    assert upstream_prefix(pairs, "dynamics") is EQUALIZED
    assert upstream_prefix(pairs, "match") is REPAIRED
    assert upstream_prefix(pairs, "repair") is None


def test_stale_output_keeps_the_stage_input_and_drops_downstream():
    pairs = rendered_pairs(match=True)
    pairs["eq"] = StagePair(MATCHED, None)
    state = resolve("eq", pairs, original=ORIGINAL, rate=48000)
    assert not state.available
    np.testing.assert_array_equal(state.before, MATCHED)
    assert resolve("output-gain", pairs, original=ORIGINAL).before is MATCHED


def test_bypassed_rendered_stage_passes_its_input_through():
    pairs = rendered_pairs(match=True, eq=True)
    state = resolve("eq", pairs, original=ORIGINAL, bypassed={"eq": True})
    assert state.available
    assert state.bypassed


def test_a_skipped_upstream_stage_is_transparent_to_the_prefix():
    """A bypassed stage passes audio through, so the prefix must walk past it."""
    # Only Repair and Matching cache their own input here, so the later stages
    # have to resolve their input by walking upstream.
    pairs = {
        "repair": StagePair(ORIGINAL, REPAIRED),
        "match": StagePair(REPAIRED, MATCHED),
        "eq": StagePair(None, EQUALIZED),
    }
    # Manual EQ is skipped, so chain-before-compression is the matching output.
    assert np.array_equal(
        resolve("dynamics", pairs, original=ORIGINAL, bypassed={"eq": True}).before, MATCHED
    )
    # Without the skip the prefix continues through the stage that actually ran.
    assert np.array_equal(resolve("dynamics", pairs, original=ORIGINAL).before, EQUALIZED)
    # Repair being applied but skipped means the chain never used that audio.
    repaired = {"repair": StagePair(ORIGINAL, REPAIRED), "eq": StagePair(None, EQUALIZED)}
    assert np.array_equal(
        resolve(
            "eq", repaired, original=ORIGINAL, fallback=ORIGINAL, bypassed={"repair": True}
        ).before,
        ORIGINAL,
    )
    assert np.array_equal(
        resolve("eq", repaired, original=ORIGINAL, fallback=ORIGINAL).before, REPAIRED
    )


def test_alignment_is_reported_rather_than_assumed():
    state = resolve("eq", rendered_pairs(eq=True), original=ORIGINAL)
    assert state.aligned
    short = StagePair(np.zeros((32, 2)), np.zeros((64, 2)))
    assert not resolve("eq", {"eq": short}, original=ORIGINAL).aligned


def test_audition_pair_refuses_unequal_sides():
    with pytest.raises(ValueError, match="same length"):
        audition_pair(np.zeros((64, 2)), np.zeros((32, 2)))


@pytest.mark.parametrize(
    ("key", "side", "expected"),
    (
        ("repair", Side.BEFORE, "original"),
        ("repair", Side.AFTER, "repaired"),
        ("mastering", Side.BEFORE, "original"),
        ("mastering", Side.AFTER, "processed"),
        ("eq", Side.BEFORE, "before EQ"),
        ("eq", Side.AFTER, "after EQ"),
        ("dynamics", Side.BEFORE, "before compression"),
        ("dynamics", Side.AFTER, "after compression"),
        ("output-gain", Side.BEFORE, "before output gain"),
        ("output-gain", Side.AFTER, "after output gain"),
    ),
)
def test_side_labels(key, side, expected):
    state = resolve(key, {}, side=side, original=ORIGINAL)
    assert state.side_label == expected
    assert state.side.processed is (side is Side.AFTER)


def test_matching_input_label_follows_the_repair_decision():
    pairs = {"repair": StagePair(ORIGINAL, REPAIRED)}
    dry = resolve("mastering", pairs, side=Side.BEFORE, original=ORIGINAL)
    wet = resolve(
        "mastering", pairs, side=Side.BEFORE, original=ORIGINAL, input_label="repaired input"
    )
    assert dry.side_label == "original"
    assert wet.side_label == "repaired input"


def test_pair_labels_name_both_signals():
    state = resolve("output-gain", rendered_pairs(output=True), original=ORIGINAL)
    assert state.labels == ("Chain before output gain", "Final output")


def test_graphs_read_raw_levels_while_transport_reads_the_matched_pair():
    pairs = rendered_pairs(eq=True)
    state = resolve("eq", pairs, side=Side.AFTER, original=ORIGINAL, rate=48000)
    np.testing.assert_array_equal(state.arrays_for(Side.BEFORE), MATCHED)
    np.testing.assert_array_equal(state.arrays_for(Side.AFTER), EQUALIZED)
    # Raw arrays are never the level-matched transport buffers.
    assert not np.array_equal(state.arrays_for(Side.AFTER), state.playback_arrays[1])
    np.testing.assert_allclose(state.playback_arrays[0], state.playback_arrays[1], atol=1e-6)
