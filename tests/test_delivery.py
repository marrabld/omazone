"""Final delivery: bit depth, headroom refusal, and optional TPDF dither."""

import numpy as np
import pytest
import soundfile as sf

from omazone.delivery import (
    CEILING,
    DeliverySettings,
    format_label,
    measure,
    measured_step,
    peak_dbfs,
    prepare,
    quantises,
    tpdf_noise,
    validate_settings,
    write,
)
from omazone.output_gain import sample_peak_db

RATE = 48000


def tone(amplitude, samples=4096, channels=2, frequency=997):
    time = np.arange(samples) / RATE
    left = amplitude * np.sin(2 * np.pi * frequency * time)
    return np.column_stack((left, -0.5 * left))[:, :channels]


def test_float_needs_no_headroom_and_is_the_default():
    settings = DeliverySettings()
    assert settings.format == "float"
    report = measure(np.full((512, 2), 4.5), settings)
    assert not report.clipped and report.fits and report.attenuation_db == 0
    assert report.steps is None and not report.dithered
    assert not quantises(settings)
    assert format_label(settings) == "32-bit float WAV"
    samples, _ = prepare(np.full((512, 2), 4.5), settings)
    np.testing.assert_array_equal(samples, np.full((512, 2), 4.5))


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_integers_refuse_full_scale_rather_than_clipping(fmt):
    at_ceiling = np.full((64, 2), CEILING[fmt] * (1 + 1e-6))
    report = measure(at_ceiling, DeliverySettings(format=fmt))
    assert report.clipped and not report.fits
    assert report.steps == int(fmt.removeprefix("pcm_"))
    with pytest.raises(ValueError, match="ceiling"):
        prepare(at_ceiling, DeliverySettings(format=fmt))
    # libsndfile would have written a clipped file. Prove we never reach it.
    sf.write("/tmp/opencode/clipcheck.wav", at_ceiling, RATE, subtype=fmt)


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_measured_attenuation_fits_and_a_short_one_is_refused(fmt):
    loud = np.full((64, 2), 1.26)
    needed = measure(loud, DeliverySettings(format=fmt)).attenuation_db
    assert needed == pytest.approx(2.01, abs=0.01)
    samples, report = prepare(loud, DeliverySettings(format=fmt), attenuation_db=-needed)
    assert report.fits and not report.clipped
    assert np.max(np.abs(samples)) <= CEILING[fmt]
    with pytest.raises(ValueError, match="still peaks"):
        prepare(loud, DeliverySettings(format=fmt), attenuation_db=-1.0)


def test_headroom_message_is_actionable_even_for_a_tiny_overshoot():
    """A fraction-of-a-dB overshoot must not read as "attenuate by 0.00 dB"."""
    with pytest.raises(ValueError) as error:
        prepare(np.full((64, 2), 1.0), DeliverySettings(format="pcm_24"))
    message = str(error.value)
    assert "0.000 dB" not in message
    assert "0.100 dB" in message
    assert "output gain" in message


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_written_files_have_the_requested_subtype_layout_and_length(fmt, tmp_path):
    audio = tone(0.5)
    path = tmp_path / f"out-{fmt}.wav"
    report = write(path, audio, RATE, DeliverySettings(format=fmt))
    assert report.fits
    info = sf.info(path)
    assert info.subtype == fmt.upper()
    assert info.samplerate == RATE
    assert info.channels == 2
    assert info.frames == len(audio)
    written, rate = sf.read(path, always_2d=True)
    assert rate == RATE and written.shape == audio.shape
    # Dither is noise, so compare envelope rather than samples.
    assert peak_dbfs(written) == pytest.approx(sample_peak_db(audio), abs=0.2)


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_unquantised_error_stays_within_one_step_and_dither_removes_it(fmt):
    audio = tone(0.25)
    settings = DeliverySettings(format=fmt, dither=False)
    quantised, _ = prepare(audio, settings)
    step = measured_step(settings)
    # Verified against the writer: the codes sit on the 2**-(bits-1) grid.
    assert step == pytest.approx(2.0 ** -(int(fmt.removeprefix("pcm_")) - 1))
    assert np.max(np.abs(quantised - audio)) <= step * 1.01
    assert np.all(np.isfinite(quantised))
    # Several dither draws differ from one another, and all stay near the input.
    runs = [
        prepare(audio, DeliverySettings(format=fmt, dither=True))[0] for _ in range(3)
    ]
    assert not np.array_equal(runs[0], runs[1])
    for result in runs:
        assert np.max(np.abs(result - audio)) <= step
        assert peak_dbfs(result) == pytest.approx(sample_peak_db(audio), abs=0.2)


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_dither_never_pushes_a_full_scale_sample_past_the_ceiling(fmt):
    audio = np.full((20000, 2), CEILING[fmt] * 0.999)
    samples, _ = prepare(audio, DeliverySettings(format=fmt, seed=3))
    assert np.max(np.abs(samples)) <= CEILING[fmt]


def test_silence_stays_silent_under_float_but_dither_reaches_below_it():
    silence = np.zeros((500, 2))
    quiet = np.full((500, 2), -1e-4)
    assert peak_dbfs(silence) == float("-inf")
    assert measure(silence, DeliverySettings(format="pcm_24")).peak_dbfs == float("-inf")
    dithered, _ = prepare(quiet, DeliverySettings(format="pcm_24", seed=1))
    undithered, _ = prepare(quiet, DeliverySettings(format="pcm_24", dither=False))
    assert peak_dbfs(dithered) > peak_dbfs(undithered)
    # TPDF peaks near, but stays below, one step: about -66 dBFS for 24-bit.
    assert peak_dbfs(dithered) < -60
    assert peak_dbfs(dithered) > -80


def test_tpdf_noise_is_triangular_bounded_and_seed_reproducible():
    first = tpdf_noise((200000, 2), 1.0, seed=11)
    second = tpdf_noise((200000, 2), 1.0, seed=11)
    np.testing.assert_array_equal(first, second)
    assert not np.array_equal(first, tpdf_noise((200000, 2), 1.0, seed=12))
    assert np.all(np.abs(first) <= 1.0 + 1e-12)
    assert abs(float(np.mean(first))) < 0.01
    # A triangular distribution on +/-1 has standard deviation 1/sqrt(6), which
    # is 1/sqrt(2) of a uniform's on the same range. Comparing the two catches a
    # half-scale or double-scale error in the amplitude.
    assert float(np.std(first)) == pytest.approx(1 / np.sqrt(6), rel=0.03)
    assert float(np.std(first)) == pytest.approx(
        float(np.std(np.random.default_rng(5).uniform(-1, 1, first.shape))) / np.sqrt(2),
        rel=0.06,
    )


@pytest.mark.parametrize("fmt", ["pcm_24", "pcm_16"])
def test_mono_and_odd_lengths_and_extremes_export(fmt, tmp_path):
    for channels, samples in ((1, 3), (2, 2), (1, 5000)):
        audio = np.full((samples, channels), 0.3)
        path = tmp_path / f"{channels}-{samples}.wav"
        write(path, audio, RATE, DeliverySettings(format=fmt, seed=2))
        info = sf.info(path)
        assert info.channels == channels and info.frames == samples
    loud = np.full((64, 2), 50.0)
    path = tmp_path / "loud.wav"
    write(path, loud, RATE, DeliverySettings(format="float"))
    written, _ = sf.read(path, always_2d=True)
    np.testing.assert_allclose(written, loud)


def test_negative_only_and_mono_overrange_are_both_refused():
    for audio in (np.full((64, 2), -1.2), np.full((64, 1), -1.2)):
        assert measure(audio, DeliverySettings(format="pcm_24")).clipped
        with pytest.raises(ValueError):
            prepare(audio, DeliverySettings(format="pcm_24"))


@pytest.mark.parametrize(
    "settings",
    [
        DeliverySettings(format="mp3"),
        DeliverySettings(format="PCM_24"),
        DeliverySettings(dither="yes"),
        DeliverySettings(seed=1.5),
        DeliverySettings(seed=True),
    ],
)
def test_invalid_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        validate_settings(settings)


def test_apply_attenuation_rejects_positive_gain_and_non_finite():
    from omazone.delivery import apply_attenuation

    audio = tone(0.5, samples=16)
    np.testing.assert_array_equal(apply_attenuation(audio, 0), audio)
    # Exactly -6.0206 dB halves the amplitude. -6.000 dB leaves 0.24% more,
    # which is the difference between the two values, not an error.
    np.testing.assert_allclose(
        apply_attenuation(audio, -6.020599913279628), audio / 2, rtol=1e-9
    )
    np.testing.assert_allclose(
        apply_attenuation(audio, -6), audio / 2, rtol=0.0025
    )
    for bad in (0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            apply_attenuation(audio, bad)


def test_delivery_never_modifies_the_render_it_is_given():
    audio = tone(0.9)
    original = audio.copy()
    for fmt in ("float", "pcm_24", "pcm_16"):
        settings = DeliverySettings(format=fmt, dither=True)
        prepare(audio, settings)
        prepare(audio, settings, attenuation_db=-12)
    np.testing.assert_array_equal(audio, original)