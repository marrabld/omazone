# Reference variation display

Match can show an optional envelope and density shading around a reference's
tonal shape. **Show envelope and density** is in the Reference variation panel
below the matching settings. New reference loads and passage captures include
the analysis automatically when enough material is available.

![Measured reference variation overlaid with the mix and output](images/omazone-reference-variation.png)

The envelope itself is a display. Ordinary reference EQ matching remains
unchanged. An optional [Range-aware matching method](range-aware-matching.md)
uses retained excerpt statistics for static correction; it has a different
normalisation from the display. No bootstrap confidence estimate is included.

## What the picture means

- The amber boundaries show the empirical 10th-90th percentile range.
- The dashed amber line is the median. It can lie between two clusters of
  observations; it is not necessarily the most common value.
- Stronger amber shading means a larger fraction of observations fell in that
  1 dB bin. The display uses actual histogram counts, not a fitted Gaussian.
- Blue is the current input, green the rendered matching output when available,
  and pale grey the reference's mean spectrum.
  Line styles also distinguish them: input solid, output dash-dot, reference
  mean dotted and median dashed.

All curves in this view use the same band-averaged relative power-density units,
in dB/Hz. They are not absolute dBFS readings. The actual requested and rendered
EQ response remains on the separate filter plot, in gain dB.

The reference mean is the existing whole-passage average PSD, which gives louder
material more influence. The envelope uses individually normalised excerpts with
equal weight. They answer different questions: the grey mean line need not stay
inside the percentile envelope.

The panel identifies **one reference passage**, its name and time bounds, and
the number of sampled excerpts accepted by the gates. This is variation within
that passage, not variation between independent reference songs. An intro and
a dense chorus can legitimately broaden the envelope. Choosing comparable
passages matters more than making a mix fit the picture.

There is no ideal-sound score, quality judgement, confidence interval or promise
that music inside the envelope sounds better. The display can be useful and
attractive without making those claims.

## Analysis policy, version 1

1. Split the chosen passage into non-overlapping, full one-second excerpts.
   Ignore a partial final second. At least four full seconds are needed.
2. Use every excerpt up to 4096. For longer material, choose 4096 evenly spaced
   excerpts across the full passage, deterministically, including the endpoints.
3. Calculate each excerpt's channel-averaged Welch power spectral density using
   Hann windows of approximately 250 ms, rounded to an even sample count and
   capped at 32768 samples, with 50% overlap
   and constant detrending. Averaging channel power avoids cancelling opposite-
   polarity stereo signals. The internal overlapping FFT windows are not counted
   as independent observations.
4. Reject excerpts below -90 dBFS equivalent spectral mean-square level or 40 dB
   below the strongest sampled excerpt, whichever gate is higher. These gates
   operate before level normalisation. Scaling level preserves the result while
   material remains above the absolute gate. Full-spectrum normalisation also
   means differing bandwidth or ultrasonic content can shift relative levels.
   Do not interpret a boundary crossing as proof of an audible EQ problem; a
   Range-aware correction therefore uses an explicit common-frequency-range policy.
5. Integrate the PSD into equally log-spaced bands from 20 Hz to the lower of
   20 kHz and Nyquist, with approximately three bands per octave. Fractional
   FFT-bin overlap is included. Divide band power by bandwidth and total spectral
   power to obtain relative power density.
6. Within each band, count observations in fixed 1 dB bins from -120 to 0 dB/Hz.
   Values at or below the floor are censored, not piled into a fabricated floor
   peak. Require four above-floor observations, coverage of at least 80% of
   accepted excerpts, and bandwidth at least as large as the FFT-bin spacing.
   Bands failing those rules have no displayed range or density.
7. Calculate linear-interpolated empirical 10th, 50th and 90th percentiles from
   each supported band's above-floor observations. Every accepted excerpt has
   equal weight, rather than loud passages getting more observations.

Duplicating a set of excerpts leaves its normalised histogram unchanged and does
not create another reference song. Finite-sample, linearly interpolated quantiles
can move with duplication because their interpolation ranks depend on the number
of observations. That is an estimator convention, not increased confidence.

Density is the histogram count divided by that band's above-floor observation
count. Every band uses the same 0-1 probability-mass scale; it is **not** stretched
to its own brightest value. Opacity follows the square root of that mass to make
less concentrated observations visible. Shading is interpolated in log frequency
for display, clipped to the percentile envelope, with a faint uniform envelope
fill. This interpolation does not create additional measured observations.
The raster uses smaller display pixels and smooth image scaling to avoid jagged
edges. Each measured 1 dB histogram bin keeps its value across its display
subdivisions; no extra histogram smoothing is applied.
Curves use shape-preserving cubic interpolation in log frequency to remove the
corners between measured bands. They still pass through every measured band
value and do not bridge missing bands. The envelope interpolates the median and
its nonnegative distances to each boundary so the boundaries cannot cross.
The density mask follows those same curves. Histogram concentration still uses
linear frequency interpolation. These are drawing choices, not finer analysis
or changes to the matching filter.
An isolated supported band is drawn across its own measured bandwidth, with no
line or shading across the unsupported bands beside it. A zero-width range is
still a line, not fabricated variation.

A constant observed value produces a line, not an invented band of variation.
Multimodal distributions can show separate concentrations, including an empty
area around the median. Neither appearance is evidence of statistical confidence.

## Targets, projects and performance

New distribution-bearing target libraries use `omazone-targets` version 2, with
an embedded `reference-variation-v2` policy, quantiles, support mask, histogram
counts and accepted excerpt-by-band observations. The displayed statistics are
unchanged from v1; retained observations support per-excerpt common-domain
normalisation for optional range-aware correction. V1 variation data remains
displayable without inventing the missing observations.
Projects retain that optional data alongside their reference targets.
Raw audio is not embedded. The mean spectrum remains unchanged, so storing or
viewing variation does not invalidate saved matching calibration.

Version 1 mean-only libraries remain loadable and remain version 1 if saved
without distribution data. They have no envelope. Reload reference audio or
capture a new passage to obtain measured variation; a mean alone cannot tell us
its distribution. Unsupported policies and malformed data are rejected instead
of silently being dropped or replaced.

Reference analysis runs in the existing loading/capture workers, not the audio
callback or GUI thread. Temporary observations are bounded by 4096 excerpts;
profiles retain summaries, a small fixed histogram and at most 4096 excerpt-by-band
observations, not audio or FFT frames. Display toggles reuse the currently valid spectrum payload and do
not repeat reference analysis or matching. Old responses cannot restore an
envelope belonging to a different target or an unloaded recording.
The view also keeps a one-entry reference-density image cache, so a changed mix
selection does not rebuild unchanged shading on the GUI thread. A different
variation analysis replaces that cached item; raw audio is not retained by it.

The toggle is a saved view preference. It changes neither processing settings,
audition gain nor export samples. The panel scrolls with settings; plots and
primary actions keep their existing space.
