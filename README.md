# Omazone

Licensed under the GNU General Public License version 3 only (`GPL-3.0-only`).
See [LICENSE](LICENSE) for the full terms. You may use, modify, and redistribute
Omazone under those terms, including commercially. Distributed derivatives must
remain GPL-licensed and provide corresponding source. There is no warranty.

A Python desktop playground for learning mastering DSP. The first experiment
is reference-matching EQ, with spectrum plots, a requested-versus-actual filter
response, offline rendering, A/B playback, and WAV export.

## Run

From this directory:

```bash
uv sync --python 3.13
uv run omazone
```

Load a mix and a reference, adjust the matching controls, then click **Analyse +
process**. Play auditions the original; the listening button switches to the
processed version at the same playback position. Click or drag the seek bar to
audition another section. Playback pauses during a drag and resumes on release
if it was playing. Play/Pause and Stop preserve the cursor; seek to the left edge
to restart. Pressing Play after reaching the end starts from the beginning.
The time display shows elapsed and total time. With the seek bar focused, arrow
keys move one second and Page Up/Down move ten seconds.
Changed settings require another render. WAV, FLAC, and AIFF mono/stereo files
are supported. Different reference and mix sample rates are supported.

The interface shows RMS and sample peaks, not LUFS or true peaks. Preview is
RMS-matched, with common attenuation to provide headroom. That is an approximate
level comparison, not perceptual loudness matching. Abrupt A/B switches can click;
crossfaded switching is a follow-up improvement.

Exports are 32-bit floating-point WAVs containing the raw EQ result, without
preview attenuation. Samples can exceed 0 dBFS; there is no limiter yet. Delay is
compensated and the output retains the input length. The complete convolution
tail is available through the processor API, but file rendering trims it.

Audio files and outputs are ignored by version control. Dependencies are pinned
in `uv.lock`.

## How it works

1. Welch analysis averages power spectra across time and channels independently.
   It does not sum stereo channels before analysis.
2. Source and reference spectra are interpolated onto a logarithmic frequency grid.
3. Their dB difference is centred to remove an overall level offset. This also
   removes the constant offset from different spectral bin widths.
4. Gaussian smoothing on the log grid reduces narrow, note-specific corrections.
   The smoothing control is the Gaussian standard deviation in octaves.
5. Boost/cut limits are applied, then scaled by the match amount. At 50% amount,
   a 6 dB maximum boost produces at most a 3 dB requested boost.
6. `scipy.signal.firwin2` designs a 2049-tap linear-phase FIR. The actual response
   can differ from the requested curve, especially at low frequencies.
7. A stateful FFT overlap-add processor renders blocks, flushes its tail, and
   the renderer compensates the FIR delay.

Analysis uses the whole file, including quiet passages. Matching an average
spectrum changes tonal balance; it cannot reproduce the reference's arrangement,
vocal balance, dynamics, or loudness. Treat the curve as an experiment rather than
a mastering verdict. Silence is rejected rather than used as a matching target.

## Code map

- `src/omazone/engine.py`: analysis, filter design, processor, offline render.
- `src/omazone/gui.py`: Qt interface, worker thread, preview playback, export.
- `tests/test_engine.py`: identity, streaming equivalence, spectral improvement,
  stereo preservation, gain limits, and preview headroom.

```bash
uv run pytest
uv run ruff check .
```

## Route to real-time

Analysis and coefficient design are separate from sample processing. The FIR
processor exposes `prepare`, `set_filter`, `reset`, `process_block`, `flush`, and
latency in samples. Coefficients are shared across channels. Updating a filter
resets state and is currently an offline operation.

The Python processor allocates and performs FFT work per block. It is a reference
implementation, not a hard-real-time callback. A later native backend can use
preallocated buffers and partitioned convolution while retaining this contract
and testing against these outputs. Filter updates will also need safe handoff
and smoothing. A DAW plugin would require a separate host wrapper and likely a
new GUI; the Qt workbench remains useful for experiments.

Useful next experiments: log-frequency analysis weighting, selectable analysis
regions, longer/minimum-phase filters, LUFS metering, crossfaded A/B, and measured
CPU/latency before adding a real-time backend.
