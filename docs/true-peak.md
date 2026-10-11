# True peak

A sample peak reads the samples you have. A **true peak** reads what a digital to
analogue converter reconstructs between them. That reconstruction can be higher,
which means a file sitting at exactly 0 dBFS sample peak can still clip on
playback. Nothing on the Output step can show you that except this measurement.

This is the third measurement in a row of three, and each answers a different
question:

| Measurement | Reads | What going above 0 dB means |
| --- | --- | --- |
| Sample peak (dBFS) | The largest sample in the file | Float samples exceed full scale |
| True peak (dBTP) | The largest sample after reconstruction | It will clip on playback |
| Loudness (LUFS) | A gated average weighted by perception | Nothing; it is not a limit |

See [output gain and peak handling](output-gain.md) and
[loudness](loudness.md).

## How it is measured

Following ITU-R BS.1770-4 Annex 2 in shape:

1. **Oversample four times.** Not by repeating samples — that invents a
   staircase with peaks the real signal does not have.
2. **Filter with a band-limited interpolator.** A Kaiser-windowed low-pass with
   a deep stop band, so the band above the original Nyquist frequency does not
   fold back in as a phantom peak.
3. **Take the largest absolute value.**

The standard's Annex 2 gives explicit coefficient tables for 48 kHz. Omazone
designs the interpolator for the file's own sample rate instead, because
recordings and references arrive at any rate and a table written for one would
misreport the rest. A 44.1 kHz file is measured on a 44.1 kHz grid.

Only three signals are measured: the original source, the signal before output
gain, and the delivered file. Oversampling every intermediate stage would cost
more for no extra information — inter-sample risk is a question about the
boundaries of the chain, not about every prefix inside it.

## What it does not do

**It does not prevent clipping.** This is a measurement. Nothing attenuates,
limits, or otherwise alters the audio on its account, and a limiter is a
separate later stage (#45). A reading above 0 dBTP is a fact about the file, not
a fault the meter has fixed. If you need the file to be safe on playback, lower
the output gain until the reading is where you want it.

**It is not loudness.** LUFS describes average perceived level; true peak
describes the worst single instant. A quiet passage can be loud on average and a
loud programme can have a low peak. They never substitute for each other.

## Where the number comes from, and why

The meter is measured in the render worker and carried with the render result,
never on the interface thread and never in the audio callback.

One detail is worth knowing because it is easy to get wrong. The interpolation
filter has not settled over the first and last few samples of a file, and the
padding `scipy.signal.resample_poly` adds there can produce a peak the signal
does not contain. Left untrimmed, a perfectly ordinary test case measures
**0.6 dB high** — reporting clean material as inter-sample hot. Those samples are
excluded, which can only ever under-report a peak that sits within a few samples
of the file's edge; it never invents one.

Long files are measured in chunks so that oversampling a six-minute stereo song
does not allocate about 1.2 GB in a single piece. Chunking is exact: the result
is identical to measuring the whole file at once, which is checked by test.

## Verification

The tests use cases whose answers are known without consulting the
implementation:

- **An exact inter-sample case.** A quarter-rate cosine offset by half a sample
  has samples at exactly ±0.7071 while the signal reaches 1.0, so sample peak is
  −3.0103 dBFS and true peak is exactly 0.0000 dBTP. Measured within 0.05 dB.
- **Peaks that land on samples.** Full-scale sines at 997 Hz, 1 kHz, 4 kHz and
  20 kHz must measure 0 dBTP, not something invented by the interpolator.
- **Calibrated oversampling tolerance.** Four-times oversampling is compared
  against a thirty-two-times reference and must agree within 0.05 dB.
- **Above full scale.** A signal scaled past 0 dBFS must be reported as such
  rather than clamped, and a pair of samples at ±1 must measure above 0 dBTP,
  which is the real-world reconstruction case.
- **Other sample rates.** The same exact case at 44.1 kHz and 96 kHz, to catch
  any 48 kHz assumption baked into the filter.

See `tests/test_truepeak.py`.

## Cost

About 1.6 seconds to oversample a six-minute stereo song, per signal measured.
This runs in the background render worker alongside integrated loudness, so the
interface stays responsive, but it adds to how long a render takes. See
[loudness](loudness.md) for the combined cost.