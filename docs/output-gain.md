# Output gain and sample peaks

The current saved processing order ends with explicit output gain:

```text
Original -> repair -> matching -> manual EQ -> compression -> output gain -> float WAV
```

An output-gain edit rerenders only the last prefix. It never changes saved
matching filters, EQ bands, compressor controls or the original file. The
stage starts bypassed at 0 dB. You can switch it off without losing its gain.

## What the three peaks say

**Original source** measures the loaded recording, **Before output gain**
measures the result of earlier processors, and **Final export** measures the
actual rendered signal that goes into the float WAV. Each is the maximum
absolute sample across all channels, displayed in dBFS. Above 0 dBFS means
the float signal contains samples with absolute value greater than 1.0. The
display says *sample peak*, not true peak.

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
the signal before and after output gain. Its separate RMS matching and common
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

Integrated LUFS and perceptually matched preview remain in #9. Oversampled
true-peak readings are #44. The ceiling limiter is a later, separate stage #45.
