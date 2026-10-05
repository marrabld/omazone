import numpy as np
import pytest

from omazone.clipping import DetectionSettings, detect_clipping, find_clipping
from omazone.declipping import RepairSettings, repair_clipping
from omazone.waveform import SampleRegion


def clipped_tone(harmonic=False):
    rate = 48000
    t = np.arange(rate // 4) / rate
    clean = 0.9 * np.sin(2 * np.pi * 440 * t)
    if harmonic:
        clean += 0.1 * np.sin(2 * np.pi * 1320 * t + 0.2)
    clean = clean[:, None]
    clipped = np.clip(clean, -0.45, 0.6)
    report = detect_clipping(clipped, SampleRegion(0, len(clipped)), DetectionSettings(0.6, -0.45))
    return rate, clean, clipped, report


@pytest.mark.parametrize("harmonic", [False, True])
def test_reconstruction_improves_known_short_clipped_waveforms(harmonic):
    rate, clean, clipped, report = clipped_tone(harmonic)
    result = repair_clipping(clipped, rate, report, report.candidates)
    assert len(result.repaired) > 100
    mask = np.zeros(clipped.shape, dtype=bool)
    for item in result.repaired:
        mask[item.start : item.end, item.channel] = True
    before = np.mean((clipped[mask] - clean[mask]) ** 2)
    after = np.mean((result.audio[mask] - clean[mask]) ** 2)
    # A richer harmonic peak is harder to infer from endpoint slopes; require
    # substantial improvement without asserting sine-level accuracy for it.
    assert after < before * (0.5 if harmonic else 0.1)
    np.testing.assert_array_equal(result.audio[~mask], clipped[~mask])
    np.testing.assert_array_equal(clipped, np.clip(clean, -0.45, 0.6))


def test_only_checked_intervals_and_channels_change_inside_selection():
    rate, clean, clipped, _ = clipped_tone()
    stereo = np.column_stack((clipped[:, 0], clean[:, 0] * 0.5))
    report = detect_clipping(
        stereo, SampleRegion(1000, 8000), DetectionSettings(0.6, -0.45, channel=0)
    )
    accepted = tuple(item for item in report.candidates if item.start > 1100 and item.end < 7900)[
        :3
    ]
    result = repair_clipping(stereo, rate, report, accepted)
    assert len(result.repaired) == 3
    mask = np.zeros(stereo.shape, dtype=bool)
    for item in accepted:
        mask[item.start : item.end, item.channel] = True
    np.testing.assert_array_equal(result.audio[~mask], stereo[~mask])
    np.testing.assert_array_equal(result.audio[:, 1], stereo[:, 1])
    assert np.any(result.audio[mask] != stereo[mask])


def test_long_runs_and_peak_bound_are_rejected_without_changes():
    rate, _, clipped, report = clipped_tone()
    for settings, reason in (
        (RepairSettings(max_run_ms=0.01), "duration"),
        (RepairSettings(max_peak_ratio=1), "bound"),
    ):
        result = repair_clipping(clipped, rate, report, report.candidates, settings)
        assert not result.repaired
        assert result.rejected
        assert all(reason in item.reason for item in result.rejected)
        np.testing.assert_array_equal(result.audio, clipped)


def test_file_edges_and_nearby_damage_do_not_supply_context():
    audio = np.zeros((100, 1))
    audio[:4] = 0.6
    audio[-4:] = -0.45
    audio[20:25] = 0.6
    audio[30:35] = 0.6
    report = detect_clipping(audio, SampleRegion(0, 100), DetectionSettings(0.6, -0.45))
    result = repair_clipping(audio, 48000, report, report.candidates)
    assert not result.repaired
    assert len(result.rejected) == 4
    assert any("boundary" in item.reason for item in result.rejected)
    assert any("Context reaches" in item.reason for item in result.rejected)
    np.testing.assert_array_equal(result.audio, audio)


def test_partial_selection_clipped_run_is_rejected():
    rate, _, clipped, full_report = clipped_tone()
    interval = full_report.candidates[3]
    region = SampleRegion(interval.start + 2, interval.end + 100)
    report = detect_clipping(clipped, region, full_report.settings)
    result = repair_clipping(clipped, rate, report, [report.candidates[0]])
    assert not result.repaired
    assert "Context reaches" in result.rejected[0].reason


def test_stale_and_duplicate_intervals_and_overload_only_are_rejected():
    rate, _, clipped, report = clipped_tone()
    accepted = report.candidates[0]
    with pytest.raises(ValueError, match="duplicated"):
        repair_clipping(clipped, rate, report, [accepted, accepted])
    stale = clipped.copy()
    stale[accepted.start : accepted.end] = 0
    with pytest.raises(ValueError, match="current plateau"):
        repair_clipping(stale, rate, report, [accepted])
    audio = np.zeros((100, 1))
    audio[40] = 1.2
    over_report = detect_clipping(audio, SampleRegion(0, 100))
    with pytest.raises(ValueError, match="plateau"):
        repair_clipping(audio, rate, over_report, over_report.overloads)


def test_intentional_flat_peak_can_be_made_worse_by_interpolation():
    # A true flat-topped waveform is ambiguous with hard clipping. Deliberately
    # checking this false positive creates error, documenting a baseline limit.
    ramp = np.arange(8) * 0.05 + 0.24
    clean = np.concatenate((np.zeros(20), ramp, np.full(10, 0.6), ramp[::-1], np.zeros(20)))[
        :, None
    ]
    report = detect_clipping(clean, SampleRegion(0, len(clean)), DetectionSettings(0.6, -0.6))
    result = repair_clipping(clean, 48000, report, report.candidates)
    assert len(result.repaired) == 1
    assert np.mean((result.audio - clean) ** 2) > 0


@pytest.mark.parametrize(
    "settings",
    [
        RepairSettings(max_run_ms=0),
        RepairSettings(context_samples=2),
        RepairSettings(max_peak_ratio=0.5),
        RepairSettings(max_run_ms=float("nan")),
    ],
)
def test_invalid_repair_parameters(settings):
    rate, _, clipped, report = clipped_tone()
    with pytest.raises(ValueError):
        repair_clipping(clipped, rate, report, report.candidates, settings)


def test_automatic_per_channel_reports_can_be_repaired():
    rate = 48000
    t = np.arange(4800) / rate
    audio = np.column_stack(
        (
            np.clip(0.9 * np.sin(2 * np.pi * 440 * t), -0.45, 0.6),
            np.clip(0.7 * np.sin(2 * np.pi * 660 * t), -0.25, 0.4),
        )
    )
    report = find_clipping(audio, SampleRegion(0, len(audio)))
    result = repair_clipping(audio, rate, report, report.candidates)
    assert {item.channel for item in result.repaired} == {0, 1}
    mask = np.zeros(audio.shape, dtype=bool)
    for item in result.repaired:
        mask[item.start : item.end, item.channel] = True
    np.testing.assert_array_equal(result.audio[~mask], audio[~mask])
