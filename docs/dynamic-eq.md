# One-band dynamic EQ

Open **Dynamics > Dynamic EQ...** to attenuate an intermittently excessive
frequency range. The band starts skipped in new and older projects. It runs
after corrective filters and before the broadband or three-band compressor;
enabling it never switches compressor mode or replaces its settings.

![One dynamic bell with its rendered gain envelope](images/omazone-dynamic-eq.png)

Set the centre **Frequency** and **Bandwidth Q**, then lower **Threshold** until
the rendered gain envelope shows the intended passages being reduced. Higher Q
is narrower. **Maximum cut** limits the requested centre attenuation. **Attack**
and **Release** set how quickly the band detector rises and recovers. Render
after changing settings, then compare by ear. Very fast timing can cause audible
modulation; high Q can ring. Use the final limiter separately when peak control
is needed.

The response graph shows the *potential* bell at maximum cut, not a promise that
the band is always applying that much. The second graph is the actual rendered
bell-gain envelope for the whole recording, sampled for display at about 100 Hz.
Maximum rendered cut is calculated from every sample, not just the plotted points.
Stage measurements follow the raw before/after pair and the selected passage.

**Listen and compare dynamic EQ** isolates this stage, with the same aligned
audition gain matching and transport as other stages. Export always contains the
full chain, independently of the audition side. Selecting a passage changes
audition and measurements, not the processing scope. Skipping the band retains
its recipe. Reset band and skip explicitly replaces its settings with defaults.

## Detector and gain law

A unity-peak, second-order band-pass filter selects the detection range. The
larger absolute band sample from either channel drives one shared envelope:

```text
detected[n] = max(abs(band[n, left]), abs(band[n, right]))
coefficient = exp(-1 / (sample_rate * time_seconds))
envelope[n] = coefficient * envelope[n-1] + (1 - coefficient) * detected[n]
level_db = 20 * log10(max(envelope, 1e-12))
cut_db = min(maximum_cut, max(0, level_db - threshold_db))
```

Attack is used when the detected value exceeds the current envelope, release
otherwise. Timing values are one-pole time constants, not a guarantee of reaching
maximum cut within that time. Threshold is a band-envelope peak level relative
to digital full scale, not RMS or whole-song LUFS. There is one dB of cut per dB
above threshold, capped at Maximum cut. This is a fixed mapping, with no ratio,
upward processing, auto threshold or makeup gain.

Both channels use the same band filter and gain control. A loud event on either
channel can therefore reduce that frequency on both sides. This preserves
channel proportions for proportional signals rather than independently shifting
their balance.

## Stable filter structure

Let `B(x)` be the fixed unity-peak band-pass output. The processed sample is:

```text
g[n] = 10 ** (-cut_db[n] / 20)
y[n] = x[n] + (g[n] - 1) * B(x)[n]
```

For constant `g`, this is a bell cut with unity response at DC and Nyquist and
gain `g` at its centre. Its feedback poles never change as the envelope moves;
only a bounded subtraction weight changes. It does not interpolate recursive
filter coefficients or reset the filter on each block. Q describes the
band-pass shape; bandwidth is frequency-warped near Nyquist and is not a fixed
Hz-wide brick-wall range. Out-of-band audio is affected less, not guaranteed
unchanged at every frequency or during transients.

Frequency/Q edits are offline operations followed by a fresh render. Frequency
is constrained to 20 Hz through the lower of 20 kHz and 45% of the sample rate,
Q to 0.5-20, threshold to -60-0 dBFS, maximum cut to 0-18 dB, attack to
0.1-200 ms, and release to 1-3000 ms. Saved precise values are retained even when
the display rounds them. Incompatible or malformed recipes are reported, never
silently clamped or replaced.

The processor adds no fixed buffering latency, retains causal IIR phase and
history across blocks, and preserves the recording length without appending an
infinite tail. A downward bell is not a limiter: time-varying filtering and phase
can still create sample or reconstructed peaks. Review Output measurements.

## Verification and limits

Tests cover bursts mixed with out-of-band tones, quiet identity, maximum cut,
release recovery, centre/end-point frequency response, fixed-pole stability,
equivalence to a fixed bell when saturated, stereo linking, block equivalence,
and rapid gain changes near frequency/Q limits. Chain tests compare explicit
processing through matching, manual EQ, corrective filters, dynamic EQ, either
compressor mode, output gain and limiting. Project and GUI tests cover additive
bypassed migration, precise recipes, isolated A/B, full-file export and upstream
cache preservation.

Automated tests do not establish audible quality on real mixes. Compare against
a static EQ cut and listen for modulation, ringing, clicks and unintended changes
outside the target range. This is one attenuation-only band; multiple bands and
upward operation are not included. The separate research in #83 remains deferred.

An active stage retains one float64 stereo render and, when prepared, two float32
audition buffers. At six minutes and 48 kHz that is about 553 MB of additional
audio buffers, excluding temporary rendering/measurement memory. Bypassed stages
reuse the upstream audio and do not eagerly allocate this stage's audition pair.
