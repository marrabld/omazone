import numpy as np
import pytest

from omazone.playback import AuditionProcessor, PlaybackCursor
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


def audition(pair, frames=17, rate=1000, processed=False):
    processor = AuditionProcessor()
    processor.prepare(rate, pair[0].shape[1], frames, processed)
    cursor = PlaybackCursor()
    # Start the tests after the initial 5 ms fade-in, unless testing that ramp.
    processor.gain = processor.gain_start = processor.gain_target = 1.0
    processor.gain_index = processor.gain_frames
    return processor, cursor


def test_ab_linear_fade_is_aligned_complementary_and_sample_exact_at_edges():
    before = np.full((100, 2), [0.8, -0.4], dtype=np.float32)
    after = -before
    pair = before, after
    processor, cursor = audition(pair)
    output = np.empty((13, 2), dtype=np.float32)
    assert not processor.fill(cursor, pair, True, output)
    weight = np.r_[np.linspace(0, 1, 10), np.ones(3)]
    expected = before[:13] * (1 - weight[:, None]) + after[:13] * weight[:, None]
    np.testing.assert_allclose(output, expected, atol=1e-7)
    np.testing.assert_array_equal(output[0], before[0])
    np.testing.assert_array_equal(output[9:], after[9:13])
    assert cursor.position == 13
    assert np.max(np.abs(output)) <= 0.8
    assert np.max(np.abs(np.diff(output[:, 0]))) < 0.18


@pytest.mark.parametrize("block_size", [1, 3, 7, 64])
def test_ab_ramps_and_loop_cursor_are_independent_of_callback_size(block_size):
    before = np.arange(100, dtype=np.float32)[:, None] / 100
    pair = before, -before
    processor, cursor = audition(pair, frames=5)
    cursor.region, cursor.loop, cursor.position = SampleRegion(20, 27), True, 25
    chunks = []
    for start in range(0, 64, block_size):
        output = np.empty((min(block_size, 64 - start), 1), dtype=np.float32)
        assert not processor.fill(cursor, pair, True, output)
        chunks.append(output)
    actual = np.concatenate(chunks)
    indices = 20 + (5 + np.arange(64)) % 7
    weight = np.r_[np.linspace(0, 1, 10), np.ones(54)]
    np.testing.assert_allclose(actual[:, 0], before[indices, 0] * (1 - 2 * weight), atol=1e-7)
    assert cursor.position == 20 + (5 + 64) % 7


def test_repeated_ab_switch_retargets_from_the_last_audible_mix():
    pair = np.ones((100, 1), dtype=np.float32), np.zeros((100, 1), dtype=np.float32)
    processor, cursor = audition(pair)
    first = np.empty((4, 1), dtype=np.float32)
    processor.fill(cursor, pair, True, first)
    reverse = np.empty((3, 1), dtype=np.float32)
    processor.fill(cursor, pair, False, reverse)
    assert reverse[0, 0] == pytest.approx(first[-1, 0])
    assert np.all(np.diff(reverse[:, 0]) >= 0)
    again = np.empty((10, 1), dtype=np.float32)
    processor.fill(cursor, pair, True, again)
    assert again[0, 0] == pytest.approx(reverse[-1, 0])
    assert again[-1, 0] == 0
    assert cursor.position == 17


@pytest.mark.parametrize("block_size", [1, 3, 11, 64])
def test_seek_ducks_moves_exactly_then_fades_in_across_callbacks(block_size):
    before = np.full((200, 2), [0.8, -0.4], dtype=np.float32)
    before[100:] *= -1
    pair = before, before.copy()
    original = before.copy()
    processor, cursor = audition(pair, frames=7)
    cursor.position = 20
    processor.request_seek(100)
    chunks = []
    for start in range(0, 20, block_size):
        block = np.empty((min(block_size, 20 - start), 2), dtype=np.float32)
        assert not processor.fill(cursor, pair, False, block)
        chunks.append(block)
    actual = np.concatenate(chunks)
    gain = np.r_[np.linspace(1, 0, 5), np.linspace(0, 1, 5), np.ones(10)]
    samples = np.r_[np.arange(20, 25), np.arange(100, 115)]
    np.testing.assert_allclose(actual, before[samples] * gain[:, None], atol=1e-7)
    assert cursor.position == 115
    assert actual[4, 0] == actual[5, 0] == 0
    np.testing.assert_array_equal(before, original)


def test_repeated_seeks_take_latest_destination_without_restarting_fade_out():
    pair = np.ones((200, 1), dtype=np.float32), np.ones((200, 1), dtype=np.float32)
    processor, cursor = audition(pair)
    processor.request_seek(50)
    first = np.empty((3, 1), dtype=np.float32)
    processor.fill(cursor, pair, False, first)
    processor.request_seek(100)
    second = np.empty((4, 1), dtype=np.float32)
    processor.fill(cursor, pair, False, second)
    np.testing.assert_allclose(np.r_[first[:, 0], second[:, 0]], [1, 0.75, 0.5, 0.25, 0, 0, 0.25])
    assert cursor.position == 102


def test_start_pause_resume_and_natural_endpoint_have_zero_edges():
    pair = np.full((30, 1), 0.6, dtype=np.float32), np.full((30, 1), 0.3, dtype=np.float32)
    processor = AuditionProcessor()
    processor.prepare(1000, 1, 3)
    cursor = PlaybackCursor()
    output = np.empty((7, 1), dtype=np.float32)
    assert not processor.fill(cursor, pair, False, output)
    np.testing.assert_allclose(output[:, 0], 0.6 * np.r_[np.linspace(0, 1, 5), 1, 1], atol=1e-7)
    processor.request_pause()
    assert processor.fill(cursor, pair, False, output)
    np.testing.assert_allclose(output[:, 0], 0.6 * np.r_[np.linspace(1, 0, 5), 0, 0], atol=1e-7)
    assert cursor.position == 12
    processor.prepare(1000, 1, 3, processed=True)
    assert not processor.fill(cursor, pair, True, output)
    assert output[0, 0] == 0
    cursor.position = 27
    assert processor.fill(cursor, pair, True, output)
    assert output[2, 0] == 0
    assert not np.any(output[3:])
    assert cursor.position == 30


def test_seek_at_eof_and_ab_during_seek_reach_the_requested_side_and_sample():
    pair = np.ones((100, 1), dtype=np.float32), np.full((100, 1), 0.2, dtype=np.float32)
    processor, cursor = audition(pair)
    cursor.position = 99
    processor.request_seek(30, SampleRegion(30, 60), True)
    output = np.empty((20, 1), dtype=np.float32)
    assert not processor.fill(cursor, pair, True, output)
    assert cursor.region == SampleRegion(30, 60) and cursor.loop
    assert cursor.position == 45
    assert output[0, 0] == 0
    np.testing.assert_allclose(output[-5:], 0.2)


def test_cancel_pause_retargets_gain_without_a_jump():
    pair = np.ones((100, 1), dtype=np.float32), np.ones((100, 1), dtype=np.float32)
    processor, cursor = audition(pair)
    processor.request_pause()
    first = np.empty((3, 1), dtype=np.float32)
    assert not processor.fill(cursor, pair, False, first)
    processor.request_resume()
    second = np.empty((7, 1), dtype=np.float32)
    assert not processor.fill(cursor, pair, False, second)
    assert second[0, 0] == first[-1, 0]
    assert second[-1, 0] == 1
    assert cursor.position == 10


def test_identical_pairs_do_not_gain_boost_or_change_stereo_balance():
    audio = np.full((100, 2), [0.8, -0.4], dtype=np.float32)
    processor, cursor = audition((audio, audio))
    output = np.empty((30, 2), dtype=np.float32)
    processor.fill(cursor, (audio, audio), True, output)
    np.testing.assert_allclose(output, audio[:30], atol=1e-7)
    assert np.max(np.abs(output)) <= 0.8


def test_zero_duration_is_sample_identical_and_invalid_duration_is_rejected():
    audio = np.arange(100, dtype=np.float32)[:, None]
    processor = AuditionProcessor(transition_ms=0)
    processor.prepare(1000, 1, 3)
    cursor = PlaybackCursor()
    output = np.empty((7, 1), dtype=np.float32)
    processor.fill(cursor, (audio, audio * 2), True, output)
    np.testing.assert_array_equal(output, audio[:7] * 2)
    for duration in (-1, np.nan, np.inf):
        with pytest.raises(ValueError):
            AuditionProcessor(duration)


@pytest.mark.parametrize("rate", [16000, 44100, 48000, 96000])
def test_fade_duration_and_correlated_peak_bound_at_supported_rates(rate):
    frames = round(rate * 0.01)
    samples = np.arange(frames * 3) / rate
    before = (0.95 * np.sin(2 * np.pi * 440 * samples)).astype(np.float32)[:, None]
    after = (0.7 * np.sin(2 * np.pi * 440 * samples + 0.1)).astype(np.float32)[:, None]
    processor, cursor = audition((before, after), frames=31, rate=rate)
    output = np.empty((frames + 10, 1), dtype=np.float32)
    processor.fill(cursor, (before, after), True, output)
    assert processor.ab_frames == frames
    weight = np.r_[np.linspace(0, 1, frames), np.ones(10)][:, None]
    expected = before[: len(output)] * (1 - weight) + after[: len(output)] * weight
    np.testing.assert_allclose(output, expected, atol=1e-7)
    assert np.max(np.abs(output)) <= 0.950001
    np.testing.assert_array_equal(output[frames:], after[frames : frames + 10])


def test_callback_reuses_prepared_audio_buffers(monkeypatch):
    pair = np.ones((1000, 2), dtype=np.float32), np.full((1000, 2), 0.2, dtype=np.float32)
    processor, cursor = audition(pair, frames=7)
    output = np.empty((17, 2), dtype=np.float32)
    scratch = processor.scratch

    def unexpected_allocation(*args, **kwargs):
        raise AssertionError("Buffer allocation in callback")

    for name in ("empty", "zeros", "ones", "arange", "linspace"):
        monkeypatch.setattr(np, name, unexpected_allocation)
    processor.request_seek(500)
    for side in (True, False, True, True):
        assert not processor.fill(cursor, pair, side, output)
    assert processor.scratch is scratch


def test_invalid_preparation_seek_and_unaligned_pair_are_rejected():
    for args in ((0, 1, 7), (1000, 3, 7), (1000, 1, 0), (1000, 1, 1.5)):
        with pytest.raises(ValueError, match="format"):
            AuditionProcessor().prepare(*args)
    processor = AuditionProcessor()
    with pytest.raises(ValueError, match="bounds"):
        processor.request_seek(-1)
    with pytest.raises(ValueError, match="bounds"):
        processor.request_seek(0, SampleRegion(5, 10))
    processor.prepare(1000, 1, 7)
    with pytest.raises(ValueError, match="matching shapes"):
        processor.fill(
            PlaybackCursor(),
            (np.ones((20, 1)), np.ones((21, 1))),
            False,
            np.empty((7, 1)),
        )
