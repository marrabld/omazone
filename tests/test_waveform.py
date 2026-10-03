import numpy as np

from omazone.waveform import PeakIndex, SampleRegion


def test_overview_preserves_impulses_and_partial_last_bucket():
    audio = np.zeros((100003, 2))
    audio[127, 0] = 0.95
    audio[513, 0] = -0.8
    audio[-1, 1] = 1.2
    index = PeakIndex(audio)
    for budget in (50, 500, 2000):
        positions, low, high = index.visible(0, len(audio), budget)
        assert len(positions) <= budget + 1
        assert np.max(high[:, 0]) == 0.95
        assert np.min(low[:, 0]) == -0.8
        assert np.max(high[:, 1]) == 1.2
        assert np.all(positions < len(audio))
    np.testing.assert_array_equal(index.peak, [0.95, 1.2])


def test_zoomed_view_has_exact_samples_and_medium_view_has_extrema():
    audio = np.random.default_rng(6).normal(size=(10000, 1))
    index = PeakIndex(audio)
    samples, low, high = index.visible(100, 120, 100)
    np.testing.assert_array_equal(samples, np.arange(100, 120))
    np.testing.assert_array_equal(low, audio[100:120])
    assert low is high
    _, low, high = index.visible(100, 5000, 100)
    assert low.min() == audio[100:5000].min()
    assert high.max() == audio[100:5000].max()


def test_region_clamps_sorts_and_uses_half_open_sample_bounds():
    assert SampleRegion.from_seconds(2, -1, 48000, 48000) == SampleRegion(0, 48000)
    assert SampleRegion.from_seconds(1 / 48000, 2 / 48000, 48000, 100) == SampleRegion(1, 2)
    assert SampleRegion.from_seconds(0.75, 0.25, 100, 100) == SampleRegion(25, 75)
    assert SampleRegion.from_seconds(5, 6, 100, 100) == SampleRegion(100, 100)
