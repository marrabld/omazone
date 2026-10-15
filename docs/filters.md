# Corrective filter core

This is the DSP-only first part of [#6](https://github.com/marrabld/omazone/issues/6).
The filters are not yet connected to the project renderer or Manual EQ controls.
Existing projects, audition and exports do not change.

## Responses and controls

`src/omazone/filters.py` provides up to six independently bypassable filters:

- Butterworth high-pass and low-pass, with 6, 12, 18 or 24 dB/octave slopes.
  These are orders one through four, not repeated identical two-pole sections.
  The specified cutoff is the -3.0103 dB point for every order.
- A true-null notch with frequency and Q. Q is centre frequency divided by
  the -3 dB bandwidth, in Hz. Notch width does not change with the slope field.
  Use bell EQ for a bounded-depth cut rather than a full notch.

Frequency must be at least 20 Hz and strictly below Nyquist. Notch Q ranges
from 0.1 to 100, and the resulting bandwidth must be below Nyquist. The slope
field applies only to high/low passes; Q applies only to the notch. All saved
control values must be valid even on bypassed filters.

SciPy designs the Butterworth filters directly as second-order sections. The
single notch biquad uses `iirnotch` and converts to SOS. No high-order transfer
polynomial is used for the Butterworth cascade. Float64 sections run in the
specified filter order using `sosfilt`, with shared coefficients and separate
histories for each channel. Empty and fully bypassed cascades copy the input
exactly.

## State, phase and tails

`FilterProcessor` follows the existing `BlockProcessor` contract. Call `prepare`
with an integer sample rate, channel count and maximum block size, then call
`set_settings`. Preparation starts bypassed; setting coefficients resets history.
`process_block` carries state across blocks; `reset` zeros history without
changing coefficients.

These operations configure an offline render. There is no smoothing or live
coefficient update support. Changing settings in the middle of playback would
discard history and can click.

`latency_samples = 0` means no fixed buffering delay. It does not mean unchanged
phase, zero group delay, or transparent transient response. These are causal IIR
filters, not linear-phase FIR crossovers. The renderer does not shift their output
to compensate frequency-dependent phase.

`render_filters` starts with zero history and preserves the input sample count.
It neither pads the start nor appends the infinite IIR tail. A signal starting
abruptly can excite a transient, and an ending can truncate the decay. Narrow
low-frequency notches can ring for seconds. A generated 50 Hz notch at Q=100
still has an impulse tail above -120 dBFS after one second; the test checks its
final 100 ms falls below -200 dBFS after a 20-second render. Lowering Q widens
the notch and shortens settling. This is a numerical check, not a listening
assessment of real recordings.

## Verification

Run `uv run pytest tests/test_filters.py -q`.

Tests cover:

- Cutoff, unity/rejection endpoints and an independent prewarped Butterworth
  magnitude formula at 44.1, 48 and 96 kHz.
- Asymptotic attenuation per prewarped octave, accounting for the digital
  filter's bilinear frequency warping near Nyquist.
- Exact-centre notch depth and numerically located -3 dB bandwidth crossings.
- Stable section poles and finite impulse output at 20 Hz and just below
  Nyquist, up to 192 kHz sample rate.
- Irregular blocks, reset, coefficient replacement, mono/stereo rendering,
  independent channel histories and preservation of stereo amplitude relations.
- Exact bypass, unchanged input, output length, two-sample recordings, combined
  response, settling and rejection of invalid control/block values.

The plotting helper returns Hz and dB on the same 8192-point grid as manual EQ.
Displayed magnitudes have a -240 dB floor. A narrow notch can fall between grid
points; its measured centre depth must not be inferred from that plot alone.

## Follow-up integration

Chain and recipe integration, individual bypass controls, a response plot and
aligned A/B remain required before #6 is complete. New controls must not alter
existing saved matching or manual EQ. Dynamic EQ #8 and selective-filtering
research #83 remain separate.
