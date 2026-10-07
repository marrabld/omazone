# Broadband compression

The processing order is repair -> learned matching -> manual EQ -> stereo-linked
compressor -> output gain -> export. The compressor can run without matching or EQ. Its settings
live in the saved project; it never relearns reference filters during rendering.

## Detector and static curve

The detector uses the largest absolute channel sample in `peak` mode or the
root-mean-square of the channel samples in `rms` mode. A single envelope follows
that linked signal. It rises using the attack time and falls using the release
time. The measured envelope feeds a downward-compression curve. Both channels
receive the same gain at each sample, preserving their instantaneous balance.

Below the knee the compressor applies no reduction. Above it, the output level
is `threshold + (input - threshold) / ratio`. A quadratic soft knee joins the
two segments smoothly across the specified width. Manual makeup gain follows
the reduction. Ratio 1:1 and makeup 0 dB make an exact identity. The engine
processes blocks with retained detector state; changing settings resets it.

No lookahead or oversampling is used. Reported latency is zero. Fast attack
cannot anticipate the start of a transient. Manual makeup can drive the output
past 0 dBFS; this stage is not a limiter. Use the output peak readout and listen.
It does not split the mix into frequency bands. A later multiband tool will need
crossovers whose uncompressed bands recombine predictably.

## What the plots mean

- The upper graph compares input level with the static output curve. The dashed
  diagonal is no change; the vertical line marks the threshold. It includes
  makeup gain when enabled, but not preview-only loudness matching.
- The middle graph plots the actual linked detector envelope in dBFS. Peak mode
  is linked across channels; RMS mode measures energy across channels *per
  sample*, followed by the same attack/release envelope. It is not an integrated
  loudness meter.
- The bottom graph shows applied negative gain reduction in dB. Both timelines
  are sampled from the processed signal at roughly 100 points per second, not
  redrawn from the static curve. The thin cursor follows playback across both
  timelines. The maximum-reduction readout measures every processed sample, so
  a brief transient between plotted points is still counted. Traces reset on each render.

**View -> Both** places the raw waveform next to the graphs. Preview A/B compares
the equalized signal with the compressed signal at the same cursor and loop. Both
sides include any earlier repair or matching. Preview RMS matching and common
headroom do not affect export. EQ-only A/B excludes the compressor.

## Project recipe and cache

The dynamics stage starts bypassed. When edited, its versioned parameters look
like this:

```json
{
  "kind": "compressor-v1",
  "threshold_db": -18.0,
  "ratio": 2.0,
  "attack_ms": 10.0,
  "release_ms": 120.0,
  "knee_db": 6.0,
  "makeup_db": 0.0,
  "detector": "peak"
}
```

The cache key includes the equalized input, bypass state and these parameters.
Editing compression rerenders compression and the later output stage. Earlier matching calibration
and manual EQ remain available. Old project files with bypassed reserved dynamics
settings retain those settings. An enabled unknown processor fails explicitly.

The offline result stores original, repaired, matched, equalized and final arrays;
the detector/reduction traces are transient and rebuilt from the original when
the project reopens. No processed audio is stored in the project JSON.

## Verification

Tests check hard/soft-knee gain, attack/release steps, peak/RMS differences,
stereo-linking, block-size independence and reset. The saved-chain checks cover
EQ -> compression order, earlier prefix reuse, bypass, persistence and explicit
unsupported-stage errors. GUI checks cover before/after-this-step listening,
saved settings, response/traces and full-chain rendering.

The demo screenshots use generated audio:

```bash
uv run python tools/render_compressor_demo.py docs/images
```
