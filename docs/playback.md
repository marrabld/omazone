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
