import numpy as np
import pytest

from omazone.playback import PlaybackCursor
from omazone.waveform import SampleRegion


@pytest.mark.parametrize("length", [1, 2, 5, 31])
@pytest.mark.parametrize("frames", [1, 3, 8, 128])
def test_loop_matches_sample_indices_across_arbitrary_wraps(length, frames):
    audio = np.arange(100 * 2).reshape(100, 2)
    region = SampleRegion(7, 7 + length)
    cursor = PlaybackCursor(region.end - 1, region, True)
    output = np.empty((frames, 2), dtype=audio.dtype)
    for _ in range(4):
        start = cursor.position
        expected = region.start + (start - region.start + np.arange(frames)) % length
        assert not cursor.fill(audio, output)
        np.testing.assert_array_equal(output, audio[expected])
        assert cursor.position == region.start + (start - region.start + frames) % length


def test_one_shot_fills_partial_block_then_stops_at_exclusive_end():
    audio = np.arange(40).reshape(20, 2)
    cursor = PlaybackCursor(4, SampleRegion(4, 9))
    output = np.empty((3, 2), dtype=audio.dtype)
    assert not cursor.fill(audio, output)
    np.testing.assert_array_equal(output, audio[4:7])
    assert cursor.fill(audio, output)
    np.testing.assert_array_equal(output, np.vstack((audio[7:9], np.zeros((1, 2)))))
    assert cursor.position == 9
    assert cursor.fill(audio, output)
    assert not np.any(output)


def test_ab_uses_one_cursor_across_loop_boundary():
    audio = np.arange(40).reshape(20, 2)
    cursor = PlaybackCursor(8, SampleRegion(4, 9), True)
    output = np.empty((3, 2), dtype=audio.dtype)
    cursor.fill(audio, output)
    np.testing.assert_array_equal(output, audio[[8, 4, 5]])
    cursor.fill(audio * 2, output)
    np.testing.assert_array_equal(output, audio[[6, 7, 8]] * 2)
    assert cursor.position == 4


def test_whole_song_exact_end_and_invalid_region():
    audio = np.arange(10).reshape(10, 1)
    output = np.empty((4, 1), dtype=audio.dtype)
    cursor = PlaybackCursor(6)
    assert cursor.fill(audio, output)
    np.testing.assert_array_equal(output, audio[6:])
    with pytest.raises(ValueError, match="nonempty"):
        PlaybackCursor(5, SampleRegion(5, 5), True).fill(audio, output)
