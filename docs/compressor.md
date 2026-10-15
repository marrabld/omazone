# Broadband compression

Dynamics also offers an optional [three-band mode](multiband.md). Broadband stays
the default, with its existing settings and detector behaviour. Switching modes
retains both recipes; it does not replace broadband with a new algorithm silently.

The processing order is repair -> learned matching -> manual EQ -> corrective filters -> stereo-linked
compressor -> output gain -> export. The compressor can run without matching or EQ. Its settings
live in the saved project; it never relearns reference filters during rendering.

## Using the dynamics page

1. Check **Enable compressor in the chain** and set threshold, ratio and timing.
   The checkbox includes or bypasses compression. It does not process audio.
2. Click **Render compression** to rebuild the chain with these settings.
   Changing a control requires another render before you can compare it.
   Rendering processes the whole song, even with a passage selected, and can take
   a while. An animated activity bar and elapsed time stay above the settings
   until processing finishes. The bar shows activity, not percentage complete.
3. Click **Loop and compare compression**, then use the listening button to switch
   between before and after. A selected passage loops; with no selection, the
   whole song plays.
4. Continue to Output or export. To omit compression, use **Skip Dynamics**, or
   uncheck the compressor and click **Render without compression**.

Before compression is the chain after repair, matching, EQ and corrective filters. Compression never
changes that input. It is not the raw source, which you can hear with
**View > Original recording**. A/B applies constant preview gain for loudness
matching and shared headroom, not compression to the before side.

Playback uses buffered audio. If the device misses buffers, a playback-dropout
warning appears. Dropouts can garble either A/B side without changing the render
or export.

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
sides include any earlier repair or matching. Preview LUFS matching and common
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
