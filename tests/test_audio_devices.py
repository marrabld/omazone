"""Device identity and exact-format preflight, without opening an audio device."""

import json

import pytest

from omazone.audio_devices import (
    AudioDeviceError,
    DeviceKey,
    check_output,
    decode_choice,
    encode_choice,
    list_outputs,
)


def device(index, name, channels=2, hostapi=0):
    return dict(
        index=index,
        name=name,
        hostapi=hostapi,
        max_output_channels=channels,
        default_samplerate=48000.0,
    )


def test_only_outputs_listed_sorted_by_backend_and_name(audio_backend):
    audio_backend.apis = [{"name": "WASAPI"}, {"name": "ASIO"}]
    audio_backend.devices = [
        device(0, "Input only", 0),
        device(1, "Zed"),
        device(2, "Alpha", hostapi=1),
        device(3, "Mono", 1),
    ]
    result = list_outputs(audio_backend)
    assert [item.index for item in result] == [2, 3, 1]
    assert result[0].key == DeviceKey("ASIO", "Alpha")
    assert result[1].channels == 1
    assert "reported default 48000 Hz" in result[0].description


@pytest.mark.parametrize("choice", [None, DeviceKey("Core Audio", "Interface 🎧")])
def test_preferences_store_names_not_session_indices(choice):
    raw = encode_choice(choice)
    assert decode_choice(raw) == choice
    assert "index" not in raw
    assert "sample_rate" not in raw


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "bad JSON",
        "[]",
        "null",
        '{"version":2,"device":null}',
        '{"version":true,"device":null}',
        '{"device":null}',
        json.dumps({"version": 1, "device": {"name": "", "hostapi": "ASIO"}}),
        json.dumps({"version": 1, "device": {"name": "A", "hostapi": "ASIO", "index": 1}}),
    ],
)
def test_unknown_preference_is_not_silently_replaced(raw):
    with pytest.raises(AudioDeviceError, match="not been replaced"):
        decode_choice(raw)
    assert decode_choice(None) is None


def test_saved_name_resolves_to_new_index_after_reordering(audio_backend):
    choice = DeviceKey("Test backend", "Interface")
    audio_backend.devices = [device(3, "Interface"), device(4, "Other")]
    assert check_output(choice, 48000, 2, audio_backend).index == 3
    audio_backend.devices = [device(3, "Other"), device(7, "Interface")]
    assert check_output(choice, 48000, 2, audio_backend).index == 7
    assert audio_backend.checks[-1] == dict(device=7, samplerate=48000, channels=2, dtype="float32")


def test_system_default_is_resolved_and_pinned_afresh_each_play(audio_backend):
    audio_backend.devices = [device(0, "Speakers"), device(1, "Headphones")]
    assert check_output(None, 44100, 1, audio_backend).index == 0
    audio_backend.default_index = 1
    assert check_output(None, 44100, 1, audio_backend).index == 1
    assert [entry["device"] for entry in audio_backend.checks] == [0, 1]
    # Reported default 48 kHz does NOT imply that 44.1 kHz is unsupported.
    assert all(entry["samplerate"] == 44100 for entry in audio_backend.checks)


def test_missing_or_ambiguous_choice_does_not_fall_back(audio_backend):
    choice = DeviceKey("Test backend", "Interface")
    with pytest.raises(AudioDeviceError, match="No fallback"):
        check_output(choice, 48000, 2, audio_backend)
    audio_backend.devices = [device(0, "Interface"), device(1, "Interface")]
    with pytest.raises(AudioDeviceError, match="cannot safely distinguish"):
        check_output(choice, 48000, 2, audio_backend)
    assert not audio_backend.checks


def test_same_name_on_different_backends_is_not_ambiguous(audio_backend):
    audio_backend.apis = [{"name": "ASIO"}, {"name": "WASAPI"}]
    audio_backend.devices = [device(0, "Interface"), device(1, "Interface", hostapi=1)]
    assert check_output(DeviceKey("WASAPI", "Interface"), 48000, 2, audio_backend).index == 1


def test_unsupported_rate_has_actionable_non_misleading_error(audio_backend):
    audio_backend.unsupported_rates.add(44100)
    with pytest.raises(AudioDeviceError) as caught:
        check_output(None, 44100, 2, audio_backend)
    text = str(caught.value)
    assert "Test output [Test backend]" in text
    assert "44100 Hz, 2 channel(s), float32" in text
    assert "default rate of 48000 Hz" in text and "not a complete list" in text
    assert "does not resample" in text and "system audio settings" in text


def test_insufficient_channel_count_and_input_only_default_are_refused(audio_backend):
    audio_backend.devices = [device(0, "Mono", 1)]
    with pytest.raises(AudioDeviceError, match="needs 2"):
        check_output(None, 48000, 2, audio_backend)
    audio_backend.devices = [device(0, "Input", 0)]
    with pytest.raises(AudioDeviceError, match="default output is unavailable"):
        check_output(None, 48000, 1, audio_backend)
    assert not audio_backend.checks


@pytest.mark.parametrize("rate,channels", [(0, 2), (48000.0, 2), (True, 2), (48000, 3), (48000, 0)])
def test_bad_playback_format_is_rejected(rate, channels, audio_backend):
    with pytest.raises(AudioDeviceError):
        check_output(None, rate, channels, audio_backend)
    assert not audio_backend.checks


def test_index_reassigned_during_preflight_is_not_used(audio_backend, monkeypatch):
    def changed(**kwargs):
        audio_backend.devices = [device(0, "Unexpected speakers")]

    monkeypatch.setattr(audio_backend, "check_output_settings", changed)
    with pytest.raises(AudioDeviceError, match="changed while preparing"):
        check_output(None, 48000, 2, audio_backend)


def test_no_backend_or_no_devices_reports_an_error_without_crashing(audio_backend):
    audio_backend.error = "PortAudio unavailable"
    with pytest.raises(AudioDeviceError, match="Could not list audio outputs"):
        list_outputs(audio_backend)
    with pytest.raises(AudioDeviceError, match="System default output is unavailable"):
        check_output(None, 48000, 2, audio_backend)
    audio_backend.error = None
    audio_backend.devices = []
    assert list_outputs(audio_backend) == ()
    with pytest.raises(AudioDeviceError):
        check_output(None, 48000, 2, audio_backend)
