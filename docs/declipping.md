# Short-interval declipping baseline

This is a learning implementation for selected hard-clipped plateaus, not a
general solution for distorted mixes. It estimates missing peaks from the
surrounding waveform; the true peaks are not recoverable with certainty.

## Reconstruction

For an accepted interval `[a, b)` in one channel:

1. Use the original samples at `a - 1` and `b` as intact endpoints.
2. Take contiguous original context on each side. Reject context at either
   clipping rail, outside those rails, or missing at file edges.
3. Fit a quadratic to each context window, constrained to the actual endpoint
   value. Its derivative at that endpoint estimates slope in amplitude/sample.
4. Evaluate a cubic Hermite curve between the endpoints, replacing only `[a, b)`.

With endpoint values `y0`, `y1`, slopes `m0`, `m1`, distance `d = b - a + 1`, and
`t = (n - a + 1) / d`, the reconstructed samples are:

```text
y(t) = (2t³ - 3t² + 1)y0 + (t³ - 2t² + t)d m0
     + (-2t³ + 3t²)y1 + (t³ - t²)d m1
```

All fitting uses the original, even when another interval has already been
reconstructed. Repairs in neighbouring channels do not change their context.
The original array is retained; output is a separate full-length array.

## Eligibility and rejections

The accepted set must be a subset of the current plateau report. A fresh scan
checks that candidates are still present in the input. Duplicates, stale masks,
and over-range-only intervals are rejected as invalid requests.

Automatic reports retain separate rails for each channel. Validation and context
checks use that channel's actual scan settings, rather than a shared stereo rail.

Individual candidates are skipped when:

- Their duration exceeds the chosen limit, initially 1 ms.
- There are fewer than the chosen 3-64 context samples on either side.
- Context reaches either clipping rail, including partial runs cut by selection.
- Entering/leaving slopes do not support a peak of the candidate's polarity.
- Reconstructed values are non-finite or exceed the per-polarity rail multiplier.
- The curve falls inside the clipping rail beyond detection tolerance.

These checks bound the estimate; they do not prove it is musically correct.
There is no general crossfade or amplitude normalisation applied to the source.
Repair export uses 64-bit float WAV to preserve the internal samples. Audition
uses separate RMS matching and common headroom, so its gain is not exported.

The basic UI exposes Find, Review, Try repair, and Listen. Manual thresholds,
reconstruction parameters, and numerical diagnostics are under Advanced; see
[the inspection guide](clipping-inspection.md).

## Reproducible experiment

Run this in the project's uv environment:

```python
import numpy as np
from omazone.clipping import DetectionSettings, detect_clipping
from omazone.declipping import repair_clipping
from omazone.waveform import SampleRegion

rate = 48000
t = np.arange(rate // 4) / rate
clean = (0.9 * np.sin(2 * np.pi * 440 * t))[:, None]
clipped = np.clip(clean, -0.45, 0.6)
report = detect_clipping(
    clipped, SampleRegion(0, len(clipped)), DetectionSettings(0.6, -0.45)
)
result = repair_clipping(clipped, rate, report, report.candidates)
mask = np.zeros(clipped.shape, dtype=bool)
for interval in result.repaired:
    mask[interval.start:interval.end, interval.channel] = True
before = np.mean((clipped[mask] - clean[mask]) ** 2)
after = np.mean((result.audio[mask] - clean[mask]) ** 2)
print(len(result.repaired), "repaired;", len(result.rejected), "skipped")
print("Repaired/clipped error ratio:", after / before)
assert np.array_equal(result.audio[~mask], clipped[~mask])
```

For this sine-wave example, the error ratio is about 0.0054. Adding a small third
harmonic produces a much harder peak shape and a ratio around 0.23 in the test
example. These measurements concern only the successfully repaired samples of
these generated inputs, not a general restoration score.

The tests also include an intentionally flat-topped waveform that the heuristic
flags as a plateau. Reconstructing its checked interval introduces error into
an otherwise correct waveform. This is a documented counterexample, not hidden
by the improvement tests. Long gaps, complex transients, noise, soft distortion,
and mixed-in clipping need other approaches or better source material.

## Next experiments

Compare iterative band-limited or sparse reconstruction with this baseline using
the same signals and listening passages. Investigate multi-region repair plans,
session persistence, and clearer context/uncertainty displays independently.
