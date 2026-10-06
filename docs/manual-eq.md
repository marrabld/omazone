# Manual EQ and the first non-destructive chain

Implemented order:

```text
Retained original -> selected repairs -> learned matching -> manual bell EQ -> export
```

Dynamics and output-limiter stages remain reserved. Enabling them fails explicitly
instead of silently omitting them. Numbered wizard navigation, multiple bands,
and continuous live parameter automation remain follow-ups.

## One bell band

The processor uses digital peaking-EQ biquad equations with frequency, gain, and
Q, represented as second-order sections. Frequency must be at least 20 Hz and
below Nyquist; gain supports +/-18 dB and Q 0.1-20. Zero gain is an exact identity
in the offline renderer.

`BellProcessor` has prepare, set_settings, reset and process_block operations.
The common `BlockProcessor` protocol specifies prepare, reset, process_block and
reported fixed latency. Configuration remains processor-specific; render wrappers
own compensation and the policy for finite versus infinite tails.
Arrays use `(frames, channels)`; history persists across irregular blocks, and
identical coefficients process both channels. Coefficient changes reset state
and are offline operations. This allocating reference implementation is not a
hard-real-time callback.

There is no lookahead or constant sample delay to compensate. The causal IIR
changes phase/group delay around the band. Its tail is mathematically infinite;
file rendering retains the original length without appending it. The preceding
matching FIR retains its existing explicit delay compensation.

## Named-region correction

Whole-recording assignment applies the filter everywhere. A named region uses
half-open `[start, end)` bounds. The filter runs through the full input so samples
inside the region have valid history, rather than restarting at a chunk boundary.

Inside the region, complementary dry/wet raised-cosine weights fade the correction
in/out. The requested duration is **per edge**, initially 75 ms. Each fade is capped
at half the region length, preventing overlap. File edges do not fade toward
nonexistent adjacent dry audio. Outside the region the input samples are copied.

This blends a fixed filtered path with dry audio instead of changing coefficients
every sample. Intermediate gain is not linear dB interpolation of the band setting.
Purple waveform windows show effective fades; the response plot shows the fully
applied bell, or unity when bypassed. Zero transition can click; short regions may
have little fully-applied area after fades shorten.

Outside-region samples remain bitwise identical **to this stage's input**, which
may already differ from the original due to earlier processing.

## Saved recipe and caching

The existing version-1 project stage parameters store:

```json
{
  "kind": "bell-v1",
  "band": {
    "frequency": 2200.0,
    "gain_db": -3.0,
    "q": 1.0,
    "region_id": "a-stable-region-id",
    "transition_ms": 75.0
  }
}
```

Use `null` region_id for the whole recording. Bypass is separate. Older reserved
EQ metadata is retained; unsupported multi-band configurations are not silently
processed or overwritten.

`ChainRenderer` exposes aligned original, repaired, matched and final arrays.
Prefix caches depend on numerical recipes and their upstream keys. EQ changes
invalidate only final processing; repair/matching changes invalidate dependent
prefixes without erasing EQ choices. Region names can change while stable IDs
and numerical bounds retain their meaning.

Rendering uses saved matching filters, never implicit matching analysis. A new
matching configuration without valid calibration asks for explicit analysis.
Changed earlier repair can reuse the chosen curve while the UI reports its stale
input analysis. Relearning remains a separate user action.

Prefix arrays are in memory; profiling large sessions and disk-backed caching are
later work. Reopening starts from the original and recipe, not an exported master,
preventing repeated effect application.

## Comparison and export

Manual EQ A/B compares **after repair/matching, before EQ** with **after EQ**, at
the same cursor and loop. It uses preview-only RMS matching and common headroom.
Changing the band disables stale output until rendering. Matching's comparison
still isolates matching, excluding the later EQ. Export contains the complete
rendered chain without preview gain.

## Verification

Tests measure center gain, DC/Nyquist unity, stable poles, state/reset and block
equivalence, exact dry masks, boundary-impulse history, shortened fades, stereo
relationships, invalid settings, prefix-cache reuse, upstream bypass, retained
calibration, and project round trips. GUI checks cover actual A/B samples through
loops, full-chain WAV export, save/reopen, bypass and reset.

Generated screenshots are reproducible with:

```bash
uv run python tools/render_manual_eq_demo.py docs/images
```
