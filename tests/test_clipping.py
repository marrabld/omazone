import numpy as np
import pytest

from omazone.clipping import DetectionSettings, detect_clipping, find_clipping, suggest_thresholds
from omazone.waveform import SampleRegion


def test_asymmetric_thresholds_absolute_bounds_isolated_peaks_and_overload():
    audio = np.zeros((32, 2))
    audio[2:7, 0] = 0.6
    audio[10:13, 0] = -0.4
    audio[17, 0] = 0.6
    audio[22:26, 0] = 0.6
    audio[27:30, 0] = 0.6
    audio[4, 1] = 1.2
    before = audio.copy()
    report = detect_clipping(audio, SampleRegion(1, 32), DetectionSettings(0.6, -0.4))
    assert [(item.channel, item.start, item.end) for item in report.candidates] == [
        (0, 2, 7),
        (0, 10, 13),
        (0, 22, 26),
        (0, 27, 30),
    ]
    assert [(item.channel, item.start, item.end) for item in report.overloads] == [(1, 4, 5)]
    assert report.stats[0].candidate_samples == 15
    assert report.stats[0].above_full_scale_samples == 0
    assert report.stats[1].above_full_scale_samples == 1
    assert report.stats[1].near_full_scale_samples == 0
    np.testing.assert_array_equal(audio, before)


def test_run_boundaries_pcm_rails_and_channel_selection():
    audio = np.zeros((20, 2))
    audio[:3, 0] = 0.999969
    audio[-4:, 0] = -1
    audio[5:9, 1] = 1
    report = detect_clipping(audio, SampleRegion(0, 20), DetectionSettings(channel=0))
    assert [(item.start, item.end) for item in report.candidates] == [(0, 3), (16, 20)]
    assert len(report.stats) == 1
    assert report.stats[0].near_full_scale_samples == 7
    assert not report.overloads


def test_scaled_clipping_can_be_suggested_below_full_scale():
    time = np.arange(4800) / 48000
    audio = np.clip(1.1 * np.sin(2 * np.pi * 440 * time), -0.45, 0.6)[:, None] * 0.8
    region = SampleRegion(0, len(audio))
    assert not detect_clipping(audio, region).candidates
    hint = suggest_thresholds(audio, region)[0]
    assert hint.positive == pytest.approx(0.48)
    assert hint.negative == pytest.approx(-0.36)
    report = detect_clipping(audio, region, DetectionSettings(hint.positive, hint.negative))
    assert len(report.candidates) > 20
    assert not report.overloads


def test_silence_dc_and_clean_signal_do_not_produce_default_candidates():
    for audio in (
        np.zeros((1000, 1)),
        np.full((1000, 1), 0.8),
        (0.8 * np.sin(2 * np.pi * 440 * np.arange(1000) / 48000))[:, None],
    ):
        report = detect_clipping(audio, SampleRegion(0, len(audio)))
        assert not report.candidates
        assert not report.overloads
    dc = np.full((1000, 1), 0.8)
    assert not detect_clipping(dc, SampleRegion(0, 1000), DetectionSettings(0.8, -0.8)).candidates
    assert suggest_thresholds(dc, SampleRegion(0, 1000))[0].positive is None


def test_tolerance_does_not_accept_a_drifting_threshold_run():
    audio = np.zeros((20, 1))
    audio[3:8, 0] = [0.59991, 0.59995, 0.6, 0.60005, 0.60009]
    assert not detect_clipping(
        audio, SampleRegion(0, 20), DetectionSettings(0.6, -0.6, 0.0001)
    ).candidates
    audio[3:8, 0] = 0.6
    assert (
        len(
            detect_clipping(
                audio, SampleRegion(0, 20), DetectionSettings(0.6, -0.6, 0.0001)
            ).candidates
        )
        == 1
    )


def test_mixing_other_audio_can_hide_clipped_plateaus():
    rng = np.random.default_rng(8)
    source = np.clip(rng.normal(size=10000), -0.6, 0.6)
    mixed = (source * 0.3 + rng.normal(0, 0.03, len(source)))[:, None]
    report = detect_clipping(mixed, SampleRegion(0, len(mixed)), DetectionSettings(0.18, -0.18))
    assert not report.candidates  # Absence of visible plateaus does not imply a clean guitar.


def test_clean_low_frequency_extrema_can_be_false_positives_at_manual_rails():
    audio = (0.8 * np.sin(2 * np.pi * 20 * np.arange(2000) / 48000))[:, None]
    region = SampleRegion(0, len(audio))
    assert not detect_clipping(audio, region).candidates
    # The heuristic cannot prove clipping when a clean extremum is sufficiently
    # flat at the chosen level. This is why candidates require visual review.
    assert detect_clipping(audio, region, DetectionSettings(0.8, -0.8)).candidates


@pytest.mark.parametrize(
    "settings",
    [
        DetectionSettings(positive=-1),
        DetectionSettings(tolerance=0),
        DetectionSettings(minimum_run=1),
        DetectionSettings(channel=3),
    ],
)
def test_invalid_controls_rejected(settings):
    with pytest.raises(ValueError):
        detect_clipping(np.zeros((10, 2)), SampleRegion(0, 10), settings)


def test_automatic_scan_uses_distinct_channel_rails():
    time = np.arange(4800) / 48000
    audio = np.column_stack(
        (
            np.clip(0.9 * np.sin(2 * np.pi * 440 * time), -0.45, 0.6),
            np.clip(0.7 * np.sin(2 * np.pi * 660 * time), -0.25, 0.4),
        )
    )
    report = find_clipping(audio, SampleRegion(0, len(audio)))
    assert report.settings_for(0).positive == pytest.approx(0.6)
    assert report.settings_for(0).negative == pytest.approx(-0.45)
    assert report.settings_for(1).positive == pytest.approx(0.4)
    assert report.settings_for(1).negative == pytest.approx(-0.25)
    assert {item.channel for item in report.candidates} == {0, 1}


def test_automatic_over_range_only_remains_separate():
    audio = np.zeros((100, 1))
    audio[40:50] = 1.2
    report = find_clipping(audio, SampleRegion(0, len(audio)))
    assert not report.candidates
    assert len(report.overloads) == 1
