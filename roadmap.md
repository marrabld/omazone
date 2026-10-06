# Omazone roadmap

Omazone is a guided, non-destructive offline mastering workbench for learning
through measurements and listening. Its approved direction is a saved project
and logical processing workflow, with the song visible throughout.

Checked items are implemented and tested; unchecked items are planned. Some
implemented work remains in feature PRs rather than `main`: see
[PR #19](https://github.com/marrabld/omazone/pull/19),
[PR #20](https://github.com/marrabld/omazone/pull/20), and
[PR #22](https://github.com/marrabld/omazone/pull/22).
This document describes development priorities, not a release schedule.

## Approved user workflow

```text
Load and listen → Mark sections → Repair → Match → Manual EQ
               → Dynamics → Output check → Export
```

Region marking remains available throughout and is shared annotation, not an
effect. Repair, matching, EQ, and dynamics can be skipped. Each step should state
its purpose, offer one obvious next action, and keep numerical controls optional.
Continue moves through the workflow without baking audio or locking earlier work.

### Persistent visual context

The song must stay visible while tools change around it. Reference targets,
mix-section assignments, and clipping controls must not replace the viewer with
a table or settings form.

- [x] A persistent song overview shows named regions, selection, and playhead.
- [x] Matching starts with spectrum/filter plots; waveform tasks start with waveforms.
- [x] The detailed viewer offers Waveform, Spectrum, and Both, remembering each tool's explicit choice.
- [x] Moving between current tools preserves mix selection, zoom/pan, cursor, and loop bounds.
- [x] The viewer identifies the signal and scope: original, current-stage input,
  or rendered output for the selected passage.
- [x] Visual processing signals are distinct from preview-only gain matching.
- [x] Reference capture adds a labelled reference pane beside the mix context,
  with independent selection and time axes. Saved spectrum-only targets show
  their curve and metadata without making the mix disappear.
- [x] Pending or stale output is labelled; previously rendered audio is not
  silently presented as the result of new settings.

The time-domain overview remains useful for locating the selected passage.
Matching hides it by default to prioritise the spectrum and filter, with an
optional View action and a persistent seek/time display. See
[the workflow and layout specification](docs/workflow.md).

## Next development milestones

1. **Saved project model, #23.** Retain source references, regions, target profiles,
   repairs, and every stage's settings. Bring persistence together rather than
   implement unrelated save formats for each tool.
2. **Fixed-order chain, #1.** Render from the retained original and saved recipe,
   with bypass/skip, latency/tails, and dependency-aware caches. Earlier edits
   preserve later choices; rerendering is distinct from explicitly relearning a target.
3. **Shared viewer and navigation, #24/#25.** Keep the song visible, reuse the
   transport/regions, and add Back/Continue/Skip over the existing tools. Viewer
   prototyping can proceed alongside the project model.
4. **Step comparisons, #27.** Compare aligned input/output of the current step
   with preview-only level matching. Preserve whole-chain comparison as an option.
5. **Manual section EQ, #26.** First new effect: bell bands with fixed frequency/Q
   and region-limited, smoothly automated gain after matching. Preserve all earlier work.
6. **Dynamics and output.** Add the broadband compressor, LUFS/true-peak metering,
   output gain, and limiter. Dynamic EQ follows the simpler EQ/detector foundation.

Initial processing order is fixed. Advanced reordering, multiband dynamics, and
real-time/plugin integration follow once this workflow is stable and understandable.

## Contributor issue backlog

These issues turn the next stages into scoped tasks with acceptance criteria and
verification suggestions. Comment on an issue if you would like to work on it;
check dependencies and discuss the approach before starting a large change.
The later experiments below remain roadmap ideas until they are scoped as issues.

| Issue | Task | Depends on |
| --- | --- | --- |
| [#1](https://github.com/marrabld/omazone/issues/1) | Fixed-order chain and prefix renderer | Project #23 |
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
| [#21](https://github.com/marrabld/omazone/issues/21) | Guided clipping repair with advanced controls hidden | Repair PR #20 |
| [#23](https://github.com/marrabld/omazone/issues/23) | Saved non-destructive project and stable region/settings model | Coordinates with #1 |
| [#24](https://github.com/marrabld/omazone/issues/24) | Persistent shared waveform/spectrum viewer | #23, #1; UI prototype can start earlier |
| [#25](https://github.com/marrabld/omazone/issues/25) | Guided workflow navigation and skipped-stage handling | #23, #24, #1 |
| [#26](https://github.com/marrabld/omazone/issues/26) | Manual parametric EQ limited to named regions | #23, #1, #6; #24/#27 for integration |
| [#27](https://github.com/marrabld/omazone/issues/27) | Aligned before/after-this-step comparison | #23, #1, #24 |

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
- [x] Guided clipping workflow with per-channel automatic scanning and optional advanced diagnostics.
- [x] Float-WAV export and automated DSP/GUI checks.
- [x] Versioned saved project, stable regions/assignments, repair replay, retained calibration, and source relinking.
- [x] Shared song viewer/overview, independent reference pane, and selection-scoped background spectrum analysis.

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
- [x] Accumulate repairs across regions and save/load their operation recipes.

Verify using known clean signals and recordings deliberately clipped at several
levels. Measure reconstruction error and listen for boundary artefacts. Include
asymmetric clipping and stereo examples. Soft or analogue distortion may require
a different approach from hard-clipped plateaus.

## 2. Saved project, modular chain, and manual filtering

Keep one application, with independently usable and testable processing modules.

- [x] Versioned project recipe with original source references, regions, target
  profiles, repair operations, processing settings, and bypass/skip state.
- [x] Save/load and revisit earlier choices without discarding other stages.
- [x] Identify dependent renders/analyses as stale; keep explicit target relearning
  separate from rendering the existing choices.
- [ ] Define a common processor contract for preparation, state reset, block
  processing, latency, and tail handling. Extend the existing FIR contract.
- [ ] Add per-module controls and bypass alongside whole-chain A/B.
- [ ] Show measurements before and after each stage.
- [ ] Add high-pass, low-pass, and notch filters with frequency-response plots.
- [ ] Make filter slope, phase behaviour, and latency explicit.
- [ ] Add parametric bell and shelving EQ bands.
- [ ] Region-limited manual bell EQ after matching, with smooth gain automation.

The approved initial processing chain is:

```text
Repair → matching EQ → manual corrective EQ → optional dynamics
       → output gain / limiting → export
```

Repair remains a file/region operation before the streaming stages. Reordering
the other modules can follow once their individual behaviour is established.
Input filtering before matching is an advanced extension rather than the default
learner workflow. Dynamic EQ can extend the corrective/dynamics tools later.

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
- [x] Save/load section plans and settings in a versioned project recipe.
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
