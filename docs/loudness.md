# Loudness

Omazone measures integrated loudness in LUFS, so you can see how loud a
programme actually is and hear a before/after comparison without the loudness
change masking what you are listening for.

This is a separate measurement from the sample peaks on the Output step, and
neither implies the other. See [output gain and peak handling](output-gain.md)
for those.

## What it measures

The meter implements ITU-R BS.1770-4:

1. **K-weighting.** Two filters model how a listener hears: a high-frequency
   shelf for the acoustic effect of a head, and an RLB high-pass below about
   100 Hz. Both are rebuilt from the standard's parameters for your sample rate,
   so a 44.1 kHz file is measured with 44.1 kHz coefficients.
2. **400 ms blocks**, stepping 100 ms.
3. **Gating.** Blocks below -70 LUFS are discarded, then blocks more than 10 LU
   below what survived are discarded too. This is why a long silence or a quiet
   ending does not drag the reported figure down.

Mono and stereo are supported, with BS.1770's channel weights of 1.0. The
meter does not read a measurement and never changes audio: it reports, and
nothing else.

## Where it appears

The Output step shows loudness beside each sample peak:

```
Original source:      -3.9 dBFS sample peak | -12.3 LUFS
Before output gain:   -3.9 dBFS sample peak | -12.3 LUFS
Final export:         -6.9 dBFS sample peak | -15.3 LUFS
```

The two columns answer different questions. A sample peak is the highest single
sample in the file. LUFS is a gated average weighted toward what is perceptually
significant. A limited track can be quiet at its peaks while still averaging
loud, and a single transient can breach 0 dBFS in an otherwise quiet programme.

## When there is no reading

Loudness is reported as `—` rather than a number when:

- the audio is silent,
- it is shorter than one 400 ms gating block,
- everything falls below the absolute -70 LUFS gate.

A dash means the measurement is undefined. A made-up figure would be worse: it
would read as a real level and quietly mislead a gain decision.

## A/B matching

Before/after playback is level-matched by integrated loudness rather than by
RMS, so the loudness change from a stage cannot hide the effect of that stage.
RMS treats a quiet passage exactly as loudly as a loud one; the gated meter does
not, so what you hear is the tonal change rather than the volume change.

Two safeguards keep this honest:

- **The gain is shared and bounded.** Both sides receive the same attenuation to
  stay under full scale, so the pair stays comparable.
- **The gain never reaches the file.** Matching affects playback only. What
  **Export WAV** writes is the rendered chain exactly as the settings produce
  it, at the output gain you chose.

When a measurement is undefined, matching falls back to RMS rather than
guessing at a level.

## What it does not do

- **It is not true peak.** LUFS cannot tell you whether a sample reconstructs
  above 0 dBFS between samples. That measurement is deliberately separate; see
  [true peak](true-peak.md).
- **It is not a limiter.** No stage in the chain limits. A final limiter (#45)
  comes after loudness and true-peak measurement are settled.
- **It is not perfectly perceptual.** BS.1770 is a good model of average
  perceived loudness, not of every listener on every programme. Matching remains
  approximate; use your ears alongside the number.

## Cost

Integrated loudness is a whole-programme measurement, so it runs once per render
across every stage prefix, in the background worker, never on the audio thread
or while the interface redraws. Expect a longer render: on a six-minute stereo
song, roughly half a second per stage.

## Verification

The tests check the meter against known levels rather than against itself. A
1 kHz sine at a stated level must read back that level, and two signals 6 dB
apart must measure exactly 6 LU apart. The K-weighting curve is checked against
the shape the standard implies — about +0.691 dB at 1 kHz, +4 dB at the top of
the band — because the standard's own offset cancels that value, so a meter that
disagrees is miscalibrated rather than merely approximate. Gating is checked by
confirming that a passage more than 10 LU down, and one below the absolute gate,
are both left out of the result.

See `tests/test_loudness.py` and `tests/test_engine.py`.