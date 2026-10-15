# Output gain and sample peaks

The current saved processing order places optional limiting after output gain:

```text
Original -> repair -> matching -> manual EQ -> filters -> compression -> output gain -> limiter -> WAV
```

An output-gain edit rerenders gain and the enabled limiter after it. It never changes saved
matching filters, EQ bands, compressor controls or the original file. The
stage starts bypassed at 0 dB. You can switch it off without losing its gain.

## What the three peaks say

**Original source** measures the loaded recording, **Before output gain**
measures the result of earlier processors, and **Final export** measures the
actual rendered signal that goes into the float WAV. Each is the maximum
absolute sample across all channels, displayed in dBFS. Above 0 dBFS means
the float signal contains samples with absolute value greater than 1.0.

When limiting is enabled, an additional **Before limiter** row measures the
signal after output gain. The reduction trace and verified-peak summary describe
the final limiter. See [the limiter](limiter.md) for its lookahead and reported
true-peak safety correction.

Each row also reports two more measurements, because one number cannot answer
every question:

| Measurement | Reads | Above 0 dB means |
| --- | --- | --- |
| Sample peak (dBFS) | The largest sample you have | Float samples exceed full scale |
| True peak (dBTP) | The largest sample after reconstruction | It will clip on playback |
| Loudness (LUFS) | A gated average over time | Nothing; it is not a limit |

Sample peak and true peak are separate readings on purpose. See
[true peak](true-peak.md). A sample peak at exactly 0 dBFS is not proof the
file will play back clean: a digital to analogue converter reconstructs the
waveform between samples, and that reconstruction can be higher.

If the source is already over 0 dBFS, it arrived that way. If the original is
below 0 but the pre-output reading is over, earlier processing added the
overload. Output gain from -36 to +18 dB scales the whole final signal; it does
not silently normalise or limit it. Lower gain can bring over-range float
samples below 0 dBFS, but it cannot reconstruct peaks flattened in a clipped
recording before import. A sample reading at exactly 0 dBFS is not proof that
the source was or was not previously clipped.

The plots and readouts refer to the *last render*. Editing gain or bypass
clears stale final readings and disables export until rendering finishes.
The source peak remains visible. Preview A/B uses the same cursor and loop for
the signal before and after output gain, excluding the limiter. Its separate LUFS matching and common
headroom do not affect the exported 32-bit float WAV.

## Recipe and verification

The output stage saves a versioned numerical recipe:

```json
{"kind": "output-gain-v1", "gain_db": -6.0}
```

Unknown older output recipes remain intact while bypassed. The gain control
cannot silently replace them. If enabled, an unknown recipe fails explicitly.
No reserved limiter settings are applied by the gain processor. Zero gain is
an exact sample identity, as is bypass.

Tests cover gain and sample peaks, existing over-range float input, a clean
source pushed over 0 by EQ, preservation of flattened sample shapes, bypass,
downstream cache reuse, save/reopen, exported float samples and output-only
listening. The generated demonstration uses no copyrighted recording:

```bash
uv run python tools/render_output_demo.py docs/images
```

Integrated LUFS is implemented; see [loudness](loudness.md). Oversampled
true-peak readings are implemented; see [true peak](true-peak.md). The ceiling
limiter is an optional separate stage after gain; see [the limiter](limiter.md).
Gain and meters alone do not prevent clipping.
