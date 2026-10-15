# Audition transitions

Playback changes only the listening signal. The original, stage renders,
measurements and exported WAV samples remain untouched.

## Switching A/B

The listening button switches sides with a complementary linear crossfade of
approximately 10 ms. Both sides read the same sample positions, including when
a selected passage loops. The cursor advances once, not once per side.

A linear fade is appropriate for strongly correlated original/processed audio.
An equal-power fade can make identical signals louder in the middle of a switch.
Complementary weights sum to one instead. Opposite-polarity signals can cancel
during the transition, but the fade does not add a gain boost.

If another switch arrives before the fade finishes, it starts from the last
audible blend rather than jumping to an endpoint. The button names the requested
side; the short fade catches up at the next audio callback.

## Seeking and pausing

A seek during playback fades the current signal to silence over approximately
5 ms, moves to the exact requested sample, then fades in over approximately
5 ms. It does not mix two different song positions or reopen the audio device.
Repeated seeks use the latest destination without repeatedly restarting the
fade-out.

Play/Pause fades to silence before stopping, retaining the cursor reached by
the fade. Starting or resuming playback fades in. Dragging the seek slider
pauses with the same fade and resumes at the chosen position on release if it
was previously playing. Seeking outside a selection returns to whole-song mode.

One-shot playback fades its final samples to silence, including a selection's
exclusive endpoint. It adds no tail and does not advance past that endpoint.
Very short passages can have overlapping start/end fades. Loops still wrap at
their exact bounds; this change does not smooth an arbitrary loop boundary.
The explicit Stop button, file loading and processing operations can still stop
the device immediately.

These durations describe the envelopes, not device latency. The existing
2048-frame buffering and high-latency device setting remain in place to protect
offline audition from dropouts.

## Callback ownership and verification

The UI publishes seek/pause requests. Only the audio callback changes a running
cursor or transition envelope. Scratch audio and ramp buffers are prepared
before the stream starts. The callback performs no Qt work, file operations,
blocking locks or audio-buffer allocation. Ramps carry their state across
callbacks and loop wraps, including callbacks larger than the prepared scratch
buffer.

Tests cover discontinuity-provoking signals, sample alignment, rapid retargeting,
mono/stereo, correlated peak bounds, supported sample rates, selection loops,
file endpoints, pause/resume and scrubbing. Existing exact routing tests disable
the envelopes; separate GUI tests exercise the default production fades.

For a real-device listening check:

- Toggle A/B on a sustained passage, then toggle repeatedly during playback.
- Seek by clicking, dragging and using the seek slider's arrow keys.
- Seek inside a loop, then outside it, and confirm the mode follows the request.
- Pause/resume and seek near the end of the file.
- Listen for clicks, dropouts or an unexpected level boost. Confirm export still
  contains the full rendered chain, not the preview fades.

Windows/macOS audible device validation remains tracked in issue #14.

## Listening device

Open **Output > Listening device** to choose an output. The list includes the
backend, output-channel capacity and reported default sample rate. Outputs with
too few channels for the current recording are disabled. System default resolves
the operating system's current output each time playback starts.

The choice is remembered in local Omazone app preferences, not in the project or
processing recipe. Named devices are stored by backend and name, never by the
numeric index that PortAudio assigns for one session. Duplicate names within a
backend cannot be distinguished safely; rename them in system settings or choose
System default explicitly. A missing saved device stays selected and blocks
playback until it returns or you choose another output.

Changing the choice stops and closes the previous stream. Press Play to start on
the new output. Refresh only updates the list; it does not switch a running
stream. PortAudio may require restarting Omazone to discover newly connected
devices. No fallback output is chosen after an error.

Before opening a stream, Omazone checks the exact recording sample rate, mono or
stereo channel count and float32 playback format. Audition is not resampled. A
device's reported default rate is not its complete set of supported rates. If
the check fails, try another output or check that device's system audio settings.
Neither this check nor device selection changes processing or export samples.
The OS audio server or device driver may perform its own conversion; Omazone
does not claim a bit-perfect hardware path.

**Copy diagnostics** copies the OS, selected output, last successfully opened
device/backend, playback format, reported stream latency, underflow count and
last error. It does not include recording or project paths. These are backend
reports, not proof of audible correctness.

### Windows and macOS listening report

Use the development build containing device selection, and record its version
or commit alongside the report in issue #14.

1. Load a mono recording, then a stereo recording. Try 44.1 and 48 kHz where the
   device supports them. Confirm stereo left/right routing with a known signal.
2. Choose System default, then your interface or headphones explicitly. Press
   Play after each change. Confirm sound comes only from the intended output.
3. Repeat the A/B, seek, loop and pause checks above. Report clicks or dropouts,
   including whether the loop endpoints themselves are discontinuous.
4. Stop, disconnect the selected external device, refresh and try Play. Confirm
   a clear error and no sound from an unintended output. Reconnect and retry;
   restart the app if PortAudio does not discover it.
5. Restart Omazone. Confirm the named choice survives, including a clear error
   if the device is absent. Load another project and confirm it does not switch
   your app-level output preference.
6. Export a float WAV and play it in another application. Confirm the full chain
   is present regardless of the audition side or device selection.
7. Paste Copy diagnostics, OS version, app version, interface model and the
   results. Note any driver/backend settings changed during the test. Do not
   upload recordings unless you have permission.
