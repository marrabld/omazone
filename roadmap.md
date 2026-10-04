# Omazone roadmap

Omazone is a workbench for learning music DSP through measurements and listening.
The first working tool is reference-matching EQ. The next goal is a modular
offline processing chain with filtering, compression, dynamic EQ, and audio repair.

This is a proposed development order, not a release schedule. Priorities can
change as we investigate real recordings and contributors bring experiments.
Checked items work in the current application; unchecked items are planned.

## Contributor issue backlog

These issues turn the next stages into scoped tasks with acceptance criteria and
verification suggestions. Comment on an issue if you would like to work on it;
check dependencies and discuss the approach before starting a large change.
The later experiments below remain roadmap ideas until they are scoped as issues.

| Issue | Task | Depends on |
| --- | --- | --- |
| [#1](https://github.com/marrabld/omazone/issues/1) | Common processor contract and offline chain | Foundation |
| [#2](https://github.com/marrabld/omazone/issues/2) | Zoomable waveform and region selection | Foundation |
| [#3](https://github.com/marrabld/omazone/issues/3) | Suspected hard-clipping detection | #2 for waveform overlay |
| [#4](https://github.com/marrabld/omazone/issues/4) | Selected-region looping with shared A/B cursor | #2 |
| [#5](https://github.com/marrabld/omazone/issues/5) | Short-interval offline declipping prototype | #2, #3 for integration |
| [#6](https://github.com/marrabld/omazone/issues/6) | High-pass, low-pass, and notch filters | #1 for integration |
| [#7](https://github.com/marrabld/omazone/issues/7) | Stereo-linked broadband compressor | #1 |
| [#8](https://github.com/marrabld/omazone/issues/8) | Single downward dynamic-EQ band | #1, #6; #7 may help |
| [#9](https://github.com/marrabld/omazone/issues/9) | LUFS metering and matched previews | Foundation |
| [#10](https://github.com/marrabld/omazone/issues/10) | Smoother A/B and seek transitions | Coordinate with #4 |
| [#11](https://github.com/marrabld/omazone/issues/11) | Output gain and overload indicators | Foundation |
| [#12](https://github.com/marrabld/omazone/issues/12) | Readable log-frequency labels | Good first issue |
| [#13](https://github.com/marrabld/omazone/issues/13) | Generated-signal matching tutorial | Good first issue |
| [#14](https://github.com/marrabld/omazone/issues/14) | Windows/macOS setup and playback validation | Good first issue |
| [#16](https://github.com/marrabld/omazone/issues/16) | Named reference-passage targets and profile files | #2 |
| [#17](https://github.com/marrabld/omazone/issues/17) | Mix-section assignments and independent correction curves | #16 |
| [#18](https://github.com/marrabld/omazone/issues/18) | Contextual section rendering and aligned transitions | #16, #17 |

Browse [all open issues](https://github.com/marrabld/omazone/issues) for current
status. Numerical prototypes can often start before their GUI integration dependencies.

## Working foundation

- [x] Whole-file spectral analysis and reference-matching EQ.
- [x] Adjustable correction amount, smoothing, and gain limits.
- [x] Requested-versus-actual FIR response plots.
- [x] Stateful block processing with delay-compensated offline rendering.
- [x] Stereo-preserving shared EQ coefficients.
- [x] RMS-matched A/B playback with shared headroom.
- [x] Scrubbing, elapsed/total time, and pause/resume.
- [x] Linked waveform plots, peak-preserving overview, and sample-accurate selection.
- [x] One-shot selected-region playback and shared-cursor A/B looping.
- [x] Independent reference selection, named spectral targets, and profile JSON save/load.
- [x] Section assignments, individual matching controls, and context-aware transition rendering.
- [x] Selected-region hard-clipping candidates, editable rail hints, and per-channel waveform markers.
- [x] Checked-candidate short-gap Hermite repair, original/repaired audition, reset, and full-precision export.
- [x] Float-WAV export and automated DSP/GUI checks.

## 1. Inspection and selected-region repair

Make it possible to identify a damaged passage and judge a repair locally.

- [x] Zoomable waveform with a region-selection tool.
- [x] Mark suspected clipped samples and runs of flattened peaks.
- [ ] Input/output peak indicators to distinguish source damage from processing overload.
- [x] Audition and loop a selected section with the existing A/B comparison.
- [ ] Inspect an actual clipped recording before choosing a reconstruction method.

### Declipping

Declipping is an offline repair operation. It estimates missing waveform peaks;
it does not recover the original recording with certainty. Gain reduction alone
can address overload in a floating-point signal, but cannot undo baked-in clipping.

- [x] Adjustable positive/negative clipping thresholds and a preview of detected intervals.
- [x] An interpolation baseline for short damaged intervals.
- [ ] Evaluate iterative band-limited or sparse reconstruction against that baseline.
- [x] Preserve samples outside the selected repair region and accepted damage mask.
- [x] A/B the repaired section with the original, retaining a resettable source.
- [ ] Accumulate repairs across regions and save/load repair sessions.

Verify using known clean signals and recordings deliberately clipped at several
levels. Measure reconstruction error and listen for boundary artefacts. Include
asymmetric clipping and stereo examples. Soft or analogue distortion may require
a different approach from hard-clipped plateaus.

## 2. Modular chain and filtering

Keep one application, with independently usable and testable processing modules.

- [ ] Define a common processor contract for preparation, state reset, block
  processing, latency, and tail handling. Extend the existing FIR contract.
- [ ] Add per-module controls and bypass alongside whole-chain A/B.
- [ ] Show measurements before and after each stage.
- [ ] Add high-pass, low-pass, and notch filters with frequency-response plots.
- [ ] Make filter slope, phase behaviour, and latency explicit.
- [ ] Add parametric bell and shelving EQ bands.

An initial chain could be:

```text
Repair → filters / corrective EQ → matching EQ → dynamic EQ → compressor → output gain
```

Repair remains a file/region operation before the streaming stages. Reordering
the other modules can follow once their individual behaviour is established.

## 3. Broadband compression

Start with one stereo-linked compressor before tackling multiband crossovers.

- [ ] Threshold, ratio, attack, release, soft knee, and manual makeup gain.
- [ ] Detector-envelope and gain-reduction displays.
- [ ] Stereo linking to avoid unintended image movement.
- [ ] Compare peak and RMS detectors.
- [ ] Optional detector-sidechain filtering.

Verify the static gain curve, attack/release behaviour, stereo linking, and
block-size independence. Listen to drums, sustained material, and mixes with
large level changes. Plot detector behaviour so the controls have visible meaning.

## 4. Dynamic EQ

Begin with a single downward-acting bell band for intermittent excess energy.

- [ ] Frequency, bandwidth, detector threshold, and maximum attenuation.
- [ ] Attack/release controls and live or rendered gain-envelope plots.
- [ ] A filter design with stable, smooth gain changes.
- [ ] Per-band bypass and stereo-linked detection.
- [ ] Multiple bands and upward processing after validating the first band.

Check that quiet passages remain unaffected, gain stays within its bounds, and
time-varying processing does not introduce clicks or instability. Compare with
a fixed EQ cut on a passage containing both normal and excessive band energy.

## 5. Metering, output control, and audition quality

These improvements can be developed alongside the processing modules.

- [ ] LUFS metering and perceptual loudness-matched A/B.
- [ ] Oversampled true-peak estimation.
- [ ] Output gain and clear overload indication.
- [ ] A modest lookahead limiter with ceiling and gain-reduction display.
- [ ] Crossfaded A/B changes and smoother seek transitions.
- [ ] Audio output-device selection.
- [ ] Export options for bit depth, headroom, and deliberate dither when quantising.

A limiter prevents new overload; declipping treats existing damage. Evaluate
them separately. Check metering against known reference results and inspect
limiter overshoot, transient distortion, and reported latency.

## 6. Section matching and further experiments

- [x] Capture named targets from independent reference passages.
- [x] Assign targets to non-overlapping mix sections, each analysed independently.
- [x] Inspect per-section curves and audition them with the existing A/B transport.
- [x] Render aligned filtered paths with complementary, bounded transition windows.
- [ ] Save/load complete section plans and settings for a particular mix.
- [ ] Rename/update captured targets without disturbing assignments.
- [ ] Evaluate transition placement/duration on real metal-to-clean arrangements.

- [x] Select which passage contributes to source/reference analysis for section matching.
- [ ] Compare spectral normalisation and frequency-weighting strategies.
- [ ] Offer longer FIR filters and show the latency/resolution tradeoff.
- [ ] Explore minimum-phase matching as an alternative to linear phase.
- [x] Save reference spectral profiles.
- [ ] Save reproducible whole-song processing settings.
- [ ] Profile memory and render time on full-length songs.

## 7. Route to real-time

Keep the Python implementation readable and useful as a numerical reference.
Move processing to a native backend only when measurements justify it.

- [ ] Benchmark processors across sample rates, channel counts, and block sizes.
- [ ] Preallocate processing buffers and separate parameter updates from callbacks.
- [ ] Implement safe coefficient handoff and smooth parameter changes.
- [ ] Evaluate partitioned FIR convolution and a C++ backend if needed.
- [ ] Add standalone real-time device processing with measured latency.
- [ ] Investigate a DAW plugin wrapper once the processing is stable.

The Qt interface need not transfer unchanged into a DAW plugin. The algorithms,
processor contracts, test signals, and Python reference outputs should carry over.

## Good first contributions

- Document setup and playback on a second operating system.
- Improve crowded logarithmic frequency labels or keyboard accessibility.
- Add a small generated-signal demonstration explaining the matching filter.
- Improve waveform navigation or prototype a filter-response panel.
- Submit a reproducible listening report with settings and timestamps.

Open an [issue](https://github.com/marrabld/omazone/issues) to discuss a task or
propose one. Contributions use the [GPLv3 license](LICENSE). Keep pull requests
focused, explain the DSP choices, and include meaningful checks for new processing.
