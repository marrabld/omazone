# Approved workflow and persistent song viewer

This is the agreed product direction, and the guided workflow it describes is now
implemented. The saved project model and persistent viewer cover repair,
matching, multi-band manual EQ, compression and explicit output gain. The fixed
render chain retains prefixes, and every stage has step-specific A/B. Navigation
runs `Listen and Mark`, `Repair`, `Match`, `Manual EQ`, `Dynamics`, `Output`,
`Export`, with each step labelled by what it still needs. See
[the workflow consolidation specification](workflow-consolidation.md) for the
design the implementation follows.

LUFS, true peaks, and limiting are the next additions, deliberately after the
consolidation landed so they attach to one comparison and status contract rather
than several.

## User journey

| Step | Purpose question | Typical visual context |
| --- | --- | --- |
| Load/listen and mark | What am I hearing, and where does the arrangement change? | Song waveform and named regions |
| Repair, optional | Is damage in the recording suitable for repair? | Zoomed waveform, candidates, original/repair overlays |
| Match, optional | Would a reference help the broad tonal balance? | Selected mix passage, reference context, spectrum and correction |
| Manual EQ, optional | What still sounds wrong to me? | Selected passage spectrum/response and its position in the song |
| Compression, optional | Are attacks or level changes too uneven? | Waveform, detector and gain-reduction history |
| Output | Is the result at a useful level without introducing overload? | Song context plus original/pre-output/final sample peaks; LUFS/true peak follow |
| Export | Which file should I deliver? | The same song/selection and an explicit rendered-chain summary |

Marking regions is always available. It is annotation shared by processors, not
a stage that transforms samples. Completing every optional stage is unnecessary.

## Layout principle

The song stays in the workspace. Tools change around the viewer:

```text
┌─────────────────────────────────────────────────────────────┐
│ Listen → Repair → Match → EQ → Dynamics → Output            │
├─────────────────────────────────────────────────────────────┤
│ Whole-song overview: named regions, selection, playback     │
├─────────────────────────────────────────────────────────────┤
│ Signal/scope label                    Waveform Spectrum Both│
│                                                             │
│             Shared detailed song viewer                     │
│             Optional labelled reference pane                │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ Current-step controls, one primary action, Advanced         │
│ Back                         Skip                 Continue  │
├─────────────────────────────────────────────────────────────┤
│ Play / pause       Loop selection       Compare this step   │
└─────────────────────────────────────────────────────────────┘
```

Use a resizable viewer/control split so a table cannot squeeze away the song.
All implemented tools use a compact inspector beside the viewer on wider displays,
or a bounded panel beneath on narrow displays. Primary actions remain fixed;
lists and detailed editors scroll within the inspector. Region naming, target
capture, section assignment and repair all keep the waveform prominent.
Matching starts with large spectrum/filter plots and a compact settings panel
beside them on wider displays, or a small controls strip below on narrow ones.
Only Match amount is initially exposed; the remaining settings are optional.
Reference targets retain mix context while giving the independent reference pane
most of the detail-view width. Other tools use the same compact View menu and
single playback row, with looping and signal selection available on demand.
Waveform is the starting view for region, reference, section, and repair tasks.
Spectrum and Both remain available without leaving the current step, and each
tool remembers explicit overrides. Preserve the user's detailed-view preference, mix
selection, zoom/pan, playback cursor, and loop bounds when navigating.

The time-domain overview locates the selected passage in the song. Matching hides
it by default to reserve space for the plots; its View menu can show it, and seeking
and elapsed/total time remain visible. Spectrum analysis should identify whether it represents
that selected passage or the full recording.

### Reference capture

Reference selection is independent of mix selection. Show a clearly labelled
reference waveform beside the retained mix context. Do not imply time/tempo
alignment between unrelated songs or let selecting a reference overwrite the
mix's marked region or playback cursor.

A saved target may contain only spectral data and passage metadata. Show those
instead of inventing a waveform or making the mix view disappear. Reference
audition, if added, must identify its distinct signal and time base.

### Which signal is visible?

Explicitly label original, input to the current step, and output of the current
step. Step comparison should coordinate the visible signal with the audible
before/after choice. Show actual processing values; preview-only level matching
must not disguise exported peaks or silently rescale analysis.

When output needs rendering, retain the waveform context and label pending/stale
results. Do not show a cached old output as though it used the new settings.

## Non-destructive project

Save a recipe containing source references, stable named regions, captured
targets, accepted repairs and their settings, EQ assignments/automation,
dynamics/output settings, and stage bypass/skip state. Retain the original.
Render from the recipe; do not use a baked intermediate file as the next session's
implicit starting point or accidentally apply effects twice.

Changing an earlier stage preserves later choices. Its affected output caches
need rendering again. Data-dependent analysis may also need refreshing, but
that is a separate action: matching must not silently relearn its correction
because the user changed repair or moved to another page.

Updating downstream manual EQ does not erase or recalibrate upstream matching.
Manual correction is allowed to move the result away from its reference; the
reference is a guide, not a constraint on the user's judgement.

## Comparison and navigation

The default comparison is **before/after this step**, not original versus the
entire accumulated chain. Use aligned signals and the same cursor/loop, with
preview-only level matching. Keep original/whole-chain comparison available as
an option. Existing RMS matching can start this work; LUFS is planned separately.

Back, Continue, and Skip preserve project choices and viewer context. Continue
does not bake audio or lock earlier steps. Clearly distinguish ready, skipped,
needs-render, and needs-analysis states. A skipped effect is a normal outcome.

## Implementation sequence

The project model, fixed chain, shared viewer, manual EQ, broadband compression,
and output gain were built first. They were then consolidated in this order, and
each step has landed and been tested:

1. Correct visible stage pairs and guard unsaved projects.
2. Give render status and comparison state one source of truth each.
3. Add the guided navigation from issue #25 without replacing the existing
   processors or viewer.
4. Add the Export step and full-session acceptance coverage.
5. Resume LUFS and true-peak metering, then a ceiling limiter. Keep export gain
   separate from preview matching.

Keep the initial chain order fixed: repair, match, manual corrective EQ, optional
dynamics, output gain, and export. Add limiting only after LUFS and true-peak
measurement. Advanced reordering and real-time/plugin work follow.
