"""Check converted, quantised files and final meters rather than source-rate estimates."""

from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf

from omazone.delivery import (
    PCM_SCALE,
    DeliverySettings,
    measure,
    measure_converted,
    suggested_attenuation,
    write,
)
from omazone.delivery_measurements import measure_blocks
from omazone.loudness import integrated_lufs
from omazone.sample_rate import converted_blocks, output_frames
from omazone.truepeak import true_peak_db


@pytest.mark.parametrize("block", [13, 257, 65536])
@pytest.mark.parametrize("rate", [44100, 48000, 96000])
def test_streaming_meters_match_existing_whole_recording_policy(rate, block):
    audio = np.random.default_rng(101).normal(0, 0.1, (rate + 37, 2))
    # Ensure a maximum near a block seam/end cannot be lost or fabricated.
    audio[-30:, -1] *= 2
    actual = measure_blocks((audio[i : i + block] for i in range(0, len(audio), block)), rate, 2)
    assert actual.frames == len(audio) and actual.peak == np.max(np.abs(audio))
    assert actual.lufs == pytest.approx(integrated_lufs(audio, rate), abs=1e-9)
    assert actual.true_peak_db == pytest.approx(true_peak_db(audio, rate), abs=1e-10)


@pytest.mark.parametrize("fmt", ["float", "pcm_24"])
def test_conversion_precedes_quantisation_and_dither_and_reports_actual_file(fmt, tmp_path):
    source, target = 48000, 44100
    audio = np.random.default_rng(7).normal(0, 0.1, (source + 37, 2))
    original = audio.copy()
    settings = DeliverySettings(format=fmt, seed=71, sample_rate=target)
    path = tmp_path / "converted.wav"
    written = write(path, audio, source, settings)
    converted = np.concatenate(list(converted_blocks(audio, source, target)))
    decoded, rate = sf.read(path, always_2d=True)
    assert rate == target and len(decoded) == output_frames(len(audio), source, target)
    if fmt == "float":
        expected = converted.astype(np.float32).astype(np.float64)
    else:
        draws = np.random.default_rng(settings.seed).random((*converted.shape, 2))
        codes = np.rint(converted * PCM_SCALE + draws[..., 0] - draws[..., 1])
        expected = codes / PCM_SCALE
    np.testing.assert_array_equal(decoded, expected)
    np.testing.assert_array_equal(audio, original)
    assert written.sample_rate == target and written.frames == len(decoded)
    assert written.peak_dbfs == pytest.approx(20 * np.log10(np.max(np.abs(decoded))), abs=1e-12)
    assert written.true_peak_db == pytest.approx(true_peak_db(decoded, target), abs=1e-10)
    assert written.lufs == pytest.approx(integrated_lufs(decoded, target), abs=1e-9)


def test_review_is_post_conversion_float_not_source_peak_or_quantisation():
    source, target = 44100, 48000
    time = np.arange(source) / source
    audio = np.sin(2 * np.pi * (source / 4) * time + np.pi / 4)[:, None]
    before = float(np.max(abs(audio)))
    measured = measure_converted(audio, source, target)
    assert measured.peak > before * 1.1
    converted = np.concatenate(list(converted_blocks(audio, source, target)))
    assert measured.peak == np.max(abs(converted))
    assert measured.lufs == pytest.approx(integrated_lufs(converted, target), abs=1e-9)


def test_converted_overload_is_refused_then_requires_explicit_attenuation(tmp_path):
    source, target = 44100, 48000
    t = np.arange(source) / source
    audio = (1.2 * np.sin(2 * np.pi * (source / 4) * t + np.pi / 4))[:, None]
    settings = DeliverySettings(format="pcm_24", seed=3, sample_rate=target)
    assert not measure(audio, settings).clipped  # Source samples alone would falsely pass.
    path = tmp_path / "master.wav"
    path.write_bytes(b"original")
    with pytest.raises(ValueError, match="Converted peak"):
        write(path, audio, source, settings)
    assert path.read_bytes() == b"original" and list(tmp_path.iterdir()) == [path]
    converted = np.concatenate(list(converted_blocks(audio, source, target)))
    reduction = suggested_attenuation(measure(converted, settings))
    written = write(path, audio, source, settings, attenuation_db=-reduction)
    assert written.fits and written.dithered
    decoded, _ = sf.read(path, always_2d=True)
    assert np.max(abs(decoded)) < 1


@pytest.mark.parametrize("when", ["conversion", "write", "meter"])
def test_failed_conversion_or_write_preserves_existing_destination_and_cleans_temp(
    tmp_path, monkeypatch, when
):
    path = tmp_path / "existing.wav"
    path.write_bytes(b"do not replace")
    audio = np.ones((48000 * 2, 2)) * 0.1
    settings = DeliverySettings(sample_rate=44100)

    def fail(*args, **kwargs):
        raise OSError("simulated delivery failure")

    if when == "conversion":
        original = __import__("omazone.delivery", fromlist=["converted_blocks"]).converted_blocks
        passes = []

        def bad_blocks(*args, **kwargs):
            passes.append(1)
            for block in original(*args, **kwargs):
                yield block
                if len(passes) > 1:
                    fail()

        monkeypatch.setattr("omazone.delivery.converted_blocks", bad_blocks)
    elif when == "write":
        monkeypatch.setattr(sf.SoundFile, "write", fail)
    else:
        monkeypatch.setattr("omazone.delivery.StreamMeter.feed", fail)
    with pytest.raises(OSError, match="simulated"):
        write(path, audio, 48000, settings)
    assert path.read_bytes() == b"do not replace"
    assert list(tmp_path.iterdir()) == [path]


def test_same_rate_export_remains_sample_exact_with_or_without_explicit_rate(tmp_path):
    audio = np.random.default_rng(99).normal(0, 0.1, (48000, 2))
    default = DeliverySettings(format="pcm_24", seed=1)
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    write(a, audio, 48000, default)
    write(b, audio, 48000, replace(default, sample_rate=48000))
    np.testing.assert_array_equal(sf.read(a, dtype="int32")[0], sf.read(b, dtype="int32")[0])


def test_undefined_silence_short_audio_and_one_frame_converted_output(tmp_path):
    for frames in (2, 100, 48000):
        audio = np.zeros((frames, 1))
        path = tmp_path / f"{frames}.wav"
        result = write(path, audio, 192000, DeliverySettings(sample_rate=44100))
        assert result.frames == output_frames(frames, 192000, 44100)
        assert result.true_peak_db is None and result.lufs is None
        assert result.peak_dbfs == float("-inf")


@pytest.mark.parametrize("rate", [0, 192000, True, 44100.0, "48000"])
def test_invalid_delivery_rate_settings_cannot_touch_an_existing_file(tmp_path, rate):
    path = tmp_path / "existing.wav"
    path.write_bytes(b"original")
    with pytest.raises(ValueError):
        write(path, np.zeros((100, 2)), 48000, DeliverySettings(sample_rate=rate))
    assert path.read_bytes() == b"original" and list(tmp_path.iterdir()) == [path]
