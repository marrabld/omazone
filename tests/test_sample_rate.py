"""Verify delivery resampling against monolithic polyphase and spectral probes."""

from math import gcd

import numpy as np
import pytest
from scipy import signal

from omazone.sample_rate import (
    INPUT_RATES,
    OUTPUT_RATES,
    conversion_filter,
    converted_blocks,
    output_frames,
)


def convert(audio, source, target, block=4096):
    return np.concatenate(list(converted_blocks(audio, source, target, block)))


@pytest.mark.parametrize("source", INPUT_RATES)
@pytest.mark.parametrize("target", OUTPUT_RATES)
def test_every_supported_pair_matches_monolithic_with_same_delay_and_rounding(source, target):
    audio = np.random.default_rng(101).normal(0, 0.1, (source // 5 + 137, 2))
    actual = convert(audio, source, target)
    divisor = gcd(source, target)
    up, down = target // divisor, source // divisor
    expected = (
        audio
        if source == target
        else signal.resample_poly(
            audio, up, down, axis=0, window=conversion_filter(up, down), padtype="constant"
        )
    )
    assert actual.shape == (output_frames(len(audio), source, target), 2)
    np.testing.assert_allclose(actual, expected, atol=2e-14, rtol=2e-14)


def test_same_rate_is_exact_views_without_filtering():
    audio = np.random.default_rng(1).normal(size=(10001, 2))
    parts = list(converted_blocks(audio, 12345, 12345, 257))
    assert all(np.shares_memory(part, audio) for part in parts)
    np.testing.assert_array_equal(np.concatenate(parts), audio)


@pytest.mark.parametrize(
    "source,target", [(48000, 44100), (44100, 48000), (192000, 44100), (8000, 96000)]
)
def test_dc_impulse_stereo_alignment_and_irregular_chunks(source, target):
    audio = np.ones((source // 2 + 37, 2)) * 0.1
    first = convert(audio, source, target, 2048)
    second = convert(audio, source, target, 12289)
    np.testing.assert_array_equal(first, second)
    trim = max(1000, round(target * 0.03))  # Beyond the zero-padded file edges.
    np.testing.assert_allclose(first[trim:-trim], 0.1, atol=2e-7)
    audio[:] = 0
    audio[len(audio) // 2] = [0.5, -0.25]
    result = convert(audio, source, target, 257)
    np.testing.assert_allclose(result[:, 0], -2 * result[:, 1], atol=1e-14)
    assert abs(np.argmax(np.abs(result[:, 0])) - len(audio) // 2 * target / source) <= 1


@pytest.mark.parametrize("source,target", [(96000, 48000), (48000, 44100), (192000, 44100)])
def test_downsampling_preserves_passband_and_rejects_aliases(source, target):
    time = np.arange(source) / source
    pass_frequency = 0.9 * min(source, target) / 2
    passed = convert(np.sin(2 * np.pi * pass_frequency * time)[:, None], source, target)
    amplitude = np.sqrt(2 * np.mean(passed[1000:-1000] ** 2))
    assert abs(20 * np.log10(amplitude)) < 0.001
    # At the destination Nyquist and beyond, downsampling must not fold
    # ultrasonic tones into the delivered audible band.
    frequency = min(source / 2 * 0.99, target / 2 * 1.02)
    rejected = convert(np.sin(2 * np.pi * frequency * time)[:, None], source, target)
    amplitude = np.sqrt(2 * np.mean(rejected[1000:-1000] ** 2))
    assert 20 * np.log10(max(amplitude, 1e-30)) < -110


def test_upsampling_rejects_first_image():
    source, target = 44100, 96000
    time = np.arange(source) / source
    result = convert(np.sin(2 * np.pi * 18000 * time)[:, None], source, target)[:, 0]
    # Use a coherent 0.5s interior to avoid measuring the finite file edges.
    interior = result[24000:72000]
    spectrum = np.abs(np.fft.rfft(interior)) / (len(interior) / 2)
    frequency = np.fft.rfftfreq(len(interior), 1 / target)
    assert abs(20 * np.log10(spectrum[np.argmin(abs(frequency - 18000))])) < 0.001
    assert 20 * np.log10(max(spectrum[np.argmin(abs(frequency - (source - 18000)))], 1e-30)) < -110


@pytest.mark.parametrize(
    "source,target",
    [(0, 48000), (48000, 0), (True, 44100), (48000.0, 44100), (48001, 44100), (48000, 192000)],
)
def test_unsupported_rate_pairs_fail_without_fallback(source, target):
    with pytest.raises(ValueError):
        convert(np.zeros((10, 2)), source, target)
