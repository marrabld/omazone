import numpy as np
import pytest
from scipy import signal

from omazone.engine import (
    FIRProcessor,
    MatchSettings,
    analyse,
    audition_pair,
    design_match,
    render,
    rms_db,
)
from omazone.loudness import integrated_lufs

# audition_pair measures loudness, so tests pass the rate explicitly.
RATE = 16000


def noise():
    return np.random.default_rng(42).normal(0, 0.1, (48000, 2))


def test_identity_target_preserves_audio():
    audio = noise()
    spectrum = analyse(audio, 48000)
    spec = design_match(spectrum, spectrum, 48000)
    np.testing.assert_allclose(render(audio, spec), audio, atol=1e-12)


def test_block_sizes_and_flush_match_direct_convolution():
    audio = noise()[:2000]
    spectrum = analyse(audio, 48000)
    reference = analyse(signal.lfilter([1], [1, -0.8], audio, axis=0), 48000)
    spec = design_match(spectrum, reference, 48000)
    expected = signal.fftconvolve(audio, spec.coefficients[:, None], axes=0)
    for size in (1, 63, 512, 4096):
        processor = FIRProcessor()
        processor.prepare(48000, 2, size)
        processor.set_filter(spec)
        blocks = [processor.process_block(audio[i : i + size]) for i in range(0, len(audio), size)]
        actual = np.concatenate([*blocks, processor.flush()])
        np.testing.assert_allclose(actual, expected, atol=1e-12)


def test_matching_reduces_broad_spectral_error():
    audio = noise()
    target_audio = signal.lfilter([1], [1, -0.8], audio, axis=0)
    source, target = analyse(audio, 48000), analyse(target_audio, 48000)
    spec = design_match(
        source, target, 48000, MatchSettings(amount=1, max_boost_db=18, max_cut_db=18)
    )
    processed = analyse(render(audio, spec), 48000)
    mask = (source.frequency > 100) & (source.frequency < 15000)

    def error(spectrum):
        delta = (spectrum.relative_db - target.relative_db)[mask]
        return np.std(delta)

    assert error(processed) < error(source) * 0.25


def test_gain_limits_and_stereo_preservation():
    mono = noise()[:, :1]
    stereo = np.concatenate((mono, -0.4 * mono), axis=1)
    source = analyse(stereo, 48000)
    target = analyse(signal.lfilter([1], [1, -0.95], stereo, axis=0), 48000)
    spec = design_match(source, target, 48000, MatchSettings(max_boost_db=3, max_cut_db=4))
    assert np.max(spec.requested_db) <= 1.5
    assert np.min(spec.requested_db) >= -2
    result = render(stereo, spec)
    np.testing.assert_allclose(result[:, 1], -0.4 * result[:, 0], atol=1e-12)


def test_preview_matches_loudness_and_keeps_headroom():
    """A/B is level-matched by LUFS, and both sides stay under full scale."""
    audio = noise() * 10
    first, second = audition_pair(audio, audio * 3, RATE)
    assert abs(integrated_lufs(first, RATE) - integrated_lufs(second, RATE)) < 0.05
    assert np.max(np.abs(first)) <= 0.950001
    assert np.max(np.abs(second)) <= 0.950001


def test_preview_falls_back_to_rms_when_loudness_is_undefined():
    """Silence and sub-block audio have no gated mean, so matching uses RMS."""
    audio = noise() * 10
    short = audio[: RATE // 10]
    first, second = audition_pair(short, short * 3, RATE)
    assert abs(rms_db(first) - rms_db(second)) < 1e-5


def test_an_identical_pair_is_not_rescaled_by_measuring_itself():
    audio = noise() * 10
    first, second = audition_pair(audio, audio, RATE)
    np.testing.assert_array_equal(first, second)
    assert np.max(np.abs(first)) <= 0.950001


def test_preview_matching_never_changes_the_audio_it_compares():
    """Matching is for listening only; the rendered array is untouched."""
    audio = noise() * 10
    before = audio.copy()
    audition_pair(audio, audio * 3, RATE)
    np.testing.assert_array_equal(audio, before)


def test_silence_rejected_and_reference_can_have_different_rate():
    with pytest.raises(ValueError, match="silent"):
        analyse(np.zeros((1000, 2)), 48000)
    audio = noise()
    source = analyse(audio, 48000)
    reference = analyse(signal.resample_poly(audio, 147, 160), 44100)
    spec = design_match(source, reference, 48000)
    assert np.all(np.isfinite(spec.coefficients))
