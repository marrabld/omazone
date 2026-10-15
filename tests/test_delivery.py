"""Check decoded WAV samples and PCM codes, not just pre-write floating audio."""

from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf

from omazone.delivery import (
    FORMATS,
    PCM_SCALE,
    PCM_STEP,
    DeliverySettings,
    apply_attenuation,
    input_ceiling,
    measure,
    peak_dbfs,
    prepare,
    suggested_attenuation,
    tpdf_noise,
    validate_settings,
    write,
)

RATE = 48000


def test_defaults_and_supported_scope():
    assert DeliverySettings() == DeliverySettings(format="float", dither=True)
    assert [key for key, _, _ in FORMATS] == ["float", "pcm_24"]
    with pytest.raises(ValueError, match="24-bit"):
        validate_settings(DeliverySettings(format="pcm_16"))


@pytest.mark.parametrize("settings", [
    None, DeliverySettings(format="mp3"), DeliverySettings(format=[]),
    DeliverySettings(dither="yes"), DeliverySettings(seed=-1),
    DeliverySettings(seed=1.5), DeliverySettings(seed=True),
])
def test_invalid_settings_are_rejected_before_creating_files(settings, tmp_path):
    path = tmp_path / "invalid.wav"
    with pytest.raises(ValueError):
        write(path, np.zeros((10, 2)), RATE, settings)
    assert not path.exists()


@pytest.mark.parametrize("polarity", [-1, 1])
@pytest.mark.parametrize("dither", [False, True])
def test_overload_is_refused_without_ever_opening_a_file(polarity, dither, tmp_path, monkeypatch):
    path = tmp_path / "must-not-exist.wav"
    settings = DeliverySettings(format="pcm_24", dither=dither)
    audio = np.full((32, 2), polarity * 1.26)
    assert measure(audio, settings).clipped

    def unexpected_open(*args, **kwargs):
        pytest.fail("Refused audio reached the WAV writer")

    monkeypatch.setattr("omazone.delivery.sf.SoundFile", unexpected_open)
    with pytest.raises(ValueError, match="ceiling"):
        write(path, audio, RATE, settings)
    assert not path.exists()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("dither", [False, True])
def test_actual_pcm_codes_follow_independent_nearest_even_reference(dither, tmp_path):
    settings = DeliverySettings(format="pcm_24", dither=dither, seed=501)
    # Half-integer boundaries, both polarities, and near-full-scale values.
    audio = np.array([
        [0.25, -0.25], [0.125, -0.125], [0.5, -0.5], [1.5, -1.5],
        [2.5, -2.5], [17.37, -17.37], [PCM_SCALE - 2, -PCM_SCALE + 2],
    ]) * PCM_STEP
    draws = np.random.default_rng(settings.seed).random((*audio.shape, 2))
    noise = draws[..., 0] - draws[..., 1] if dither else 0
    expected_codes = np.rint(audio * PCM_SCALE + noise).astype(np.int64)
    path = tmp_path / "codes.wav"
    report = write(path, audio, RATE, settings)
    codes, rate = sf.read(path, dtype="int32", always_2d=True)
    np.testing.assert_array_equal(codes.astype(np.int64), expected_codes * 256)
    assert np.all(codes % 256 == 0)
    decoded, _ = sf.read(path, dtype="float64", always_2d=True)
    np.testing.assert_array_equal(decoded, expected_codes / PCM_SCALE)
    assert report.peak_dbfs == pytest.approx(peak_dbfs(decoded), abs=1e-12)
    assert report.bits == 24 and report.dithered is dither and report.fits
    info = sf.info(path)
    assert info.subtype == "PCM_24" and info.frames == len(audio)
    assert info.channels == 2 and rate == RATE


@pytest.mark.parametrize("phase", [0.0, 0.125, 0.375, 0.5, 0.875])
def test_written_tpdf_error_is_unbiased_and_variance_does_not_follow_signal(phase, tmp_path):
    # Standard non-subtractive 2-LSB-peak-to-peak TPDF gives total error
    # variance 1/4 LSB^2, independent of position within a quantisation bin.
    audio = np.full((160000, 2), (127 + phase) * PCM_STEP)
    path = tmp_path / "dither-statistics.wav"
    write(path, audio, RATE, DeliverySettings(format="pcm_24", seed=71))
    decoded, _ = sf.read(path, always_2d=True)
    error_lsb = (decoded - audio) / PCM_STEP
    assert abs(float(error_lsb.mean())) < 0.005
    assert float(error_lsb.var()) == pytest.approx(0.25, abs=0.004)
    assert abs(float(np.corrcoef(error_lsb.T)[0, 1])) < 0.01


def test_tpdf_has_full_one_lsb_peak_and_expected_variance():
    noise = tpdf_noise((200000, 2), PCM_STEP, seed=123)
    np.testing.assert_array_equal(noise, tpdf_noise(noise.shape, PCM_STEP, seed=123))
    assert np.max(np.abs(noise)) < PCM_STEP
    assert np.max(np.abs(noise)) > 0.99 * PCM_STEP
    assert float(noise.var()) / PCM_STEP**2 == pytest.approx(1 / 6, abs=0.002)
    assert not np.array_equal(noise, tpdf_noise(noise.shape, PCM_STEP, seed=124))


@pytest.mark.parametrize("polarity", [-1, 1])
def test_dither_remains_full_amplitude_at_the_input_ceiling(polarity, tmp_path):
    settings = DeliverySettings(format="pcm_24", seed=31)
    audio = np.full((50000, 2), polarity * input_ceiling(settings))
    path = tmp_path / "boundary.wav"
    report = write(path, audio, RATE, settings)
    decoded, _ = sf.read(path, always_2d=True)
    assert len(np.unique(decoded)) == 3, "Dither was reduced or silently disabled"
    assert report.dithered and report.fits
    assert decoded.max() <= 1 - PCM_STEP and decoded.min() >= -1
    unsafe = np.full((32, 2), polarity * np.nextafter(input_ceiling(settings), np.inf))
    with pytest.raises(ValueError):
        write(path, unsafe, RATE, settings)


@pytest.mark.parametrize("format", ["float", "pcm_24"])
def test_explicit_attenuation_applies_even_when_audio_already_fits(format, tmp_path):
    audio = np.full((2000, 2), 0.25)
    original = audio.copy()
    settings = DeliverySettings(format=format, dither=False)
    path = tmp_path / "attenuation.wav"
    write(path, audio, RATE, settings, attenuation_db=-20 * np.log10(2))
    decoded, _ = sf.read(path, always_2d=True)
    np.testing.assert_array_equal(decoded, 0.125)
    np.testing.assert_array_equal(audio, original)


def test_suggested_reduction_rounds_up_and_fits_without_suppressing_dither(tmp_path):
    settings = DeliverySettings(format="pcm_24", seed=3)
    for peak in (1.0, 1.26, 5):
        audio = np.full((2000, 2), peak)
        report = measure(audio, settings)
        reduction = suggested_attenuation(report)
        assert reduction >= report.attenuation_db and reduction >= 0.01
        prepared, prepared_report = prepare(audio, settings, -reduction)
        assert prepared_report.dithered and np.ptp(prepared) > 0
        path = tmp_path / f"reduced-{peak}.wav"
        written_report = write(path, audio, RATE, settings, -reduction)
        decoded, _ = sf.read(path, always_2d=True)
        np.testing.assert_array_equal(decoded, prepared)
        assert written_report.peak_dbfs == pytest.approx(peak_dbfs(decoded), abs=1e-12)
        with pytest.raises(ValueError):
            write(path, audio, RATE, settings, 0)
    assert suggested_attenuation(measure(np.zeros((2, 1)), settings)) == 0


@pytest.mark.parametrize("gain", [True, 1, np.nan, np.inf, "-6", 1j])
def test_invalid_attenuation_rejected_for_quiet_audio(gain, tmp_path):
    audio = np.full((10, 1), 0.25)
    with pytest.raises(ValueError):
        apply_attenuation(audio, gain)
    with pytest.raises(ValueError):
        write(tmp_path / "bad-gain.wav", audio, RATE, DeliverySettings(), gain)


@pytest.mark.parametrize("format", ["float", "pcm_24"])
def test_silence_channel_layout_length_and_repeatability(format, tmp_path):
    settings = DeliverySettings(format=format, seed=11)
    for length, channels in ((2, 1), (131073, 2), (1001, 3)):
        audio = np.zeros((length, channels))
        first = tmp_path / "first.wav"
        second = tmp_path / "second.wav"
        write(first, audio, RATE, settings)
        write(second, audio, RATE, settings)
        decoded, rate = sf.read(first, always_2d=True)
        repeated, _ = sf.read(second, always_2d=True)
        # Float WAV headers can contain a PEAK-chunk timestamp. Compare audio
        # codes, not whole-file bytes that may differ across a second boundary.
        np.testing.assert_array_equal(decoded, repeated)
        prepared, _ = prepare(audio, settings)
        np.testing.assert_array_equal(decoded, prepared)
        assert decoded.shape == audio.shape and rate == RATE
        if format == "float":
            np.testing.assert_array_equal(decoded, 0)
        else:
            assert decoded.min() >= -PCM_STEP and decoded.max() <= PCM_STEP
        # Silence without dither stays exactly zero.
        write(second, audio, RATE, replace(settings, dither=False))
        decoded, _ = sf.read(second, always_2d=True)
        np.testing.assert_array_equal(decoded, 0)


def test_unseeded_integer_exports_use_fresh_noise(tmp_path):
    audio = np.zeros((2000, 2))
    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    settings = DeliverySettings(format="pcm_24")
    write(first, audio, RATE, settings)
    write(second, audio, RATE, settings)
    a, _ = sf.read(first, always_2d=True)
    b, _ = sf.read(second, always_2d=True)
    assert not np.array_equal(a, b)


def test_undithered_low_tone_has_half_lsb_error_bound(tmp_path):
    audio = (1e-5 * np.sin(2 * np.pi * 997 * np.arange(48000) / RATE))[:, None]
    path = tmp_path / "tone.wav"
    write(path, audio, RATE, DeliverySettings(format="pcm_24", dither=False))
    decoded, _ = sf.read(path, always_2d=True)
    assert np.max(np.abs(decoded - audio)) <= PCM_STEP / 2
    assert abs(float((decoded - audio).mean())) < 0.005 * PCM_STEP


def test_float_preserves_headroom_but_refuses_infinite_casts(tmp_path):
    settings = DeliverySettings()
    path = tmp_path / "float.wav"
    audio = np.full((30, 2), 4.5)
    report = write(path, audio, RATE, settings)
    decoded, _ = sf.read(path, always_2d=True)
    np.testing.assert_array_equal(decoded, audio.astype(np.float32))
    assert not report.dithered and report.bits is None
    for invalid in (np.full((10, 2), 1e100), np.full((10, 2), np.nan)):
        original = path.read_bytes()
        with pytest.raises(ValueError):
            write(path, invalid, RATE, settings)
        assert path.read_bytes() == original
    assert peak_dbfs(np.full((2, 1), 1e-20)) == pytest.approx(-400)
    assert peak_dbfs(np.zeros((2, 1))) == float("-inf")


def test_partial_write_failure_leaves_existing_destination_untouched(tmp_path, monkeypatch):
    path = tmp_path / "precious.wav"
    path.write_bytes(b"Do not truncate me")

    def fail(*args, **kwargs):
        raise OSError("Simulated disk error")

    monkeypatch.setattr(sf.SoundFile, "write", fail)
    with pytest.raises(OSError, match="disk error"):
        write(path, np.zeros((1000, 2)), RATE, DeliverySettings(format="pcm_24"))
    assert path.read_bytes() == b"Do not truncate me"
    assert list(tmp_path.iterdir()) == [path]


def test_rate_validation_preserves_existing_destination(tmp_path):
    path = tmp_path / "existing.wav"
    path.write_bytes(b"unchanged")
    for rate in (0, -1, 48000.0, True):
        with pytest.raises(ValueError):
            write(path, np.zeros((2, 1)), rate, DeliverySettings())
        assert path.read_bytes() == b"unchanged"
