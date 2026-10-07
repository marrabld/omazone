# Workflow consolidation

This milestone makes the implemented processors behave as one application before
more processors or meters are added. It does not change the DSP order or add LUFS,
true-peak measurement, limiting, plugin support, or arbitrary stage reordering.

The workflow remains:

```text
Listen and mark -> Repair -> Match -> Manual EQ -> Dynamics -> Output -> Export
```

Repair, Match, Manual EQ, Dynamics, and output gain are optional. The Output
review step remains in the workflow even when output gain is skipped. Region
marking is shared project annotation, not a processing stage.

## What already works

Retain these implemented behaviours:

- The original recording and project recipe remain separate from rendered audio.
- The processing order is fixed and prefix caches avoid unnecessary work.
- The song viewer, selection, zoom, cursor, loop, and reference context persist
  while tools change.
- Each implemented processing stage has bypass state and a stage comparison.
- Changing an earlier stage keeps later settings and invalidates affected output.
- Export is unavailable when the full-chain render is stale.

The consolidation work should reuse these pieces rather than replace them.

## Audit findings

The audit followed four journeys: a simple load-adjust-export session, a complete
repair-to-output session, an earlier-stage edit after a full render, and a saved
project reopened for further work.

| ID | Priority | Observed behaviour | Required outcome |
| --- | --- | --- | --- |
| WC-01 | Correctness | Manual EQ and Compression can display the final downstream output while their A/B plays the correct stage output. The graph and audio can therefore describe different signals. | Playback, waveform, spectrum, labels, and measurements must use the same current-stage input/output pair. |
| WC-02 | Data safety | Closing, opening, creating a project, or replacing the mix can discard a dirty project without confirmation. | Every destructive transition offers Save, Discard, or Cancel. It proceeds only after a successful save or explicit discard. |
| WC-03 | Navigation | Eight implementation-oriented tabs put Matching first and Repair fifth. Regions, target management, and section matching look like peer effects. | Top-level navigation follows the seven-step user workflow. Region tools remain available throughout; target and section tools live within Match. |
| WC-04 | Guidance | Loading only a mix lands on a disabled reference-matching action even though matching is optional. | The first useful path does not require a reference. The interface offers a clear next step or an explicit skip. |
| WC-05 | Comparison | Comparison choice, visible signal, loop, and playback side are controlled in several places. Output rendering can still leave an original-only view selected. | One comparison state determines the pair, selected side, availability, labels, visible data, and playback data. Rendering selects the rendered stage's comparison. |
| WC-06 | Status | Ready, needs-render, and needs-analysis messages are inferred and can disagree between the viewer, action bar, inspector, and Export button. | One source of truth for render state and one for matching-analysis state supply all visible status text and action availability. Retained but old matching analysis is distinct from stale rendered audio. |
| WC-07 | Skip/bypass | Stage enablement appears as Project-menu bypass actions, local Apply checkboxes, or no local control. | Each optional stage has one canonical enabled/skipped state and consistent wording. Menu actions, if retained, are synchronized shortcuts. |
| WC-08 | Export | Export is a global button without a review of active stages, render freshness, or final measurements. | Export is the last workflow step and shows the chain summary, final measurements, readiness, and destination action. |
| WC-09 | Persistence | Viewer context is saved but changing it does not mark the project dirty. | Treat viewer context as session metadata captured on save. Restore it on reopen, but do not mark the processing recipe dirty for navigation or playback changes alone. |
| WC-10 | Matching context | A saved section recipe can be presented as a whole-song correction after a generic render. | Render completion describes the saved matching mode: none, whole recording, or sections. |

## Shared behaviour contract

### Navigation

- Top-level steps are Listen and Mark, Repair, Match, Manual EQ, Dynamics,
  Output, and Export, in that order.
- Direct step selection and Back are always available.
- Continue changes the current step only. It never renders, bakes audio, or relearns
  a reference target. It is available only when the current processing stage is
  Ready or Skipped. When unavailable, visible text says whether to configure,
  analyse, render, or skip the stage.
- Continue is always available from Listen and Mark. From Output it is available
  when the full-chain render is Ready, whether output gain is enabled or skipped.
  Export is the final step and has no Continue control.
- Skip changes the current optional stage to skipped and advances. Existing settings
  remain saved. Enabling the stage later restores them.
- Listen and Mark, Output review, and Export cannot be skipped. Output gain can be
  enabled or skipped within Output.
- Named regions remain available from every step.
- Whole-song matching, reference targets, and section matching are modes or
  supporting views within Match, not top-level workflow steps.

### Rendering and analysis

Every processing stage has one render state:

| State | Meaning | Allowed actions |
| --- | --- | --- |
| Blocked | A required source or reference asset is missing or incompatible. | Load or Relink. |
| Not configured | The optional stage has no operation configured. | Configure or Skip. |
| Skipped | The saved recipe bypasses the stage. | Continue or Enable. |
| Needs render | Settings are valid but rendered audio does not reflect them. | Render. Comparison output, final measurements, and export are unavailable. |
| Ready | Rendered audio reflects current settings. | Compare, Continue, or Export when the full chain is ready. |

Matching also has an analysis state:

| State | Meaning | Effect |
| --- | --- | --- |
| Current | The learned curve reflects the current input and matching settings. | Rendering and export are allowed. |
| Retained | A compatible curve was learned from an earlier input. | Rendering and export are allowed, with a visible retained-analysis label. |
| Missing | The requested matching mode has no compatible learned curve. | Analysis or Skip is required before rendering. |

- Analysis learns data from a recording or reference. Rendering applies the saved
  recipe. The application never substitutes one action for the other.
- An optional stage with no configured operation is Not configured. Continue and
  Export remain unavailable until the user configures or explicitly skips it.
- Editing an earlier stage preserves all later settings and marks affected rendered
  prefixes stale.
- Cached output from old settings is never labelled as current output.

### Comparison, playback, and the viewer

Every comparison has a named pair:

| Comparison | Before | After |
| --- | --- | --- |
| Repair | Original recording | Repaired recording |
| Matching | Input to matching, including repair | Matching output |
| Manual EQ | Chain before manual EQ | Chain after manual EQ |
| Dynamics | Chain before compression | Chain after compression |
| Output | Chain before output gain | Final output |
| Whole chain | Original recording | Final output |

- Original-only listening is separate from a before/after comparison.
- The selected comparison and side control both audible and visible data.
- Before and After use the same cursor, selection, loop bounds, and scope.
- Changing sides does not change the current position or enable looping.
- Looping is an independent transport choice. A comparison control must not also
  turn looping on.
- A stage-local render selects that stage's comparison and starts on Before.
  Project -> Render saved recipe preserves the current comparison when it remains
  valid; otherwise it selects Whole chain on Before.
- If After is unavailable, the UI gives the reason beside the control. A tooltip
  may repeat the reason but is not the sole explanation.
- Preview-only level matching affects playback only. Graphs, measurements, and
  export use unmodified processing levels.

### Measurements

- Every measurement names its signal and scope, such as "Final output, whole
  recording" or "Before compression, selected passage."
- Measurements derived from stale audio disappear. The status explains that a new
  render is required.
- Sample peak, true peak, and loudness are different measurements and must retain
  those names.
- Stage views show measurements for their own input/output pair. Output and Export
  show measurements for the final chain.

### Project safety and persistence

- A dirty project prompts before New, Open, replacement mix, or window close.
- Save, Discard, and Cancel have their ordinary meanings. A failed or cancelled
  save does not continue the destructive action.
- If the user chooses Save, defer New, Open, source replacement, or Close. Resume
  it only from the successful save callback. A cancelled Save As dialog, save
  error, or close request while saving leaves the current project open. Replacing
  a reference is an ordinary project edit, not source replacement.
- Processing choices, bypass state, regions, and sources are project data.
- Selection, zoom, cursor, loop, active step, and viewer mode are session metadata.
  Saving captures them and reopening restores them, but changes to this context
  alone do not mark the processing recipe dirty.

### Export

- Export always writes the current full-chain render, never a preview buffer.
- The Export step lists every stage's render state in processing order and shows
  the separate matching-analysis state.
- Export shows whether matching analysis is current, the final sample peak, and
  later the integrated LUFS and true peak when those measurements exist.
- If output is stale, Export identifies the earliest action needed and provides a
  route to that step. It does not offer a stale file as current.

## Interface proposal

The first implementation should rearrange existing widgets rather than redesign
each processor.

```text
+--------------------------------------------------------------------------+
| Omazone   Project name                         Load mix   Load reference  |
+--------------------------------------------------------------------------+
| Listen & Mark | Repair | Match | EQ | Dynamics | Output | Export         |
|                 each step shows Ready, Skipped, or Action needed         |
+--------------------------------------------------------------------------+
| Whole-song overview: regions, selection, cursor                          |
+-------------------------------------------+------------------------------+
| Comparing: Manual EQ                     | Manual EQ                    |
| Viewing and listening: Before EQ         | Purpose and short status     |
| [Before] [After]       [Waveform v]       |                              |
|                                           | Existing settings            |
| Existing waveform/spectrum/reference      | Advanced settings            |
| viewer                                    |                              |
|                                           | [Apply EQ and render]        |
+-------------------------------------------+------------------------------+
| [Back]                 [Skip this step]                      [Continue]  |
+--------------------------------------------------------------------------+
| [Play/Pause] [Stop] [Loop selection]                    time and seek    |
+--------------------------------------------------------------------------+
```

On narrow windows, the existing splitter continues to put the inspector below
the viewer. The workflow navigation may scroll horizontally, but Back and Continue
remain visible.

### Step contents

| Step | Existing content to reuse | Primary action |
| --- | --- | --- |
| Listen and Mark | Regions page and persistent overview | Name selection |
| Repair | Clipping inspector | Its existing Find, Review, Repair, Listen progression |
| Match | Matching page with Reference targets and Mix sections as secondary views | Analyse and process, or Render saved match |
| Manual EQ | Manual EQ view | Apply EQ and render |
| Dynamics | Compressor view | Apply compression and render |
| Output | Output gain view | Render and check final output |
| Export | New compact chain summary using existing export code | Export WAV |

## Delivery sequence

Each pull request must leave the application usable and add journey-level tests.

### PR 1: correctness and project safety

- Fix WC-01 under issue #52 by deriving visible pairs from the same chain prefixes
  as playback.
- Fix Output's original-only post-render comparison state.
- Add Save, Discard, Cancel guards under issue #51 for New, Open, replacement mix,
  and close.
- Add tests for stage-pair identity, output comparison focus, and each dirty-project
  transition.

This PR comes first because the current behaviour can misrepresent audio or lose
work. It does not alter navigation.

### PR 2: shared render and analysis status

- Implement issue #54 with one render-state calculation and the separate
  matching-analysis state.
- Make navigation, action bar, inspectors, measurements, and Export availability
  consume those states.
- Keep the existing tabs during the refactor.
- Add table-driven tests for every render and analysis state, upstream edits,
  bypass identity, and save/reopen.

### PR 3: shared comparison state

- Finish issue #27 with one comparison model for pair, side, availability, and labels.
- Make the workspace, transport, action bar, and inspectors consume it.
- Keep the existing tabs during the refactor.
- Test each stage's exact `ChainResult` prefixes, stale comparisons, bypass identity,
  and cursor/loop preservation.

### PR 4: guided navigation

- Replace the eight top-level tool tabs with the seven workflow steps.
- Add Back, Continue, and contextual Skip/Enable controls.
- Show stage status in navigation and preserve direct step access.
- Add keyboard and narrow-window tests.

### PR 5: matching tools and Export

- Place target management and section matching within Match.
- Implement issue #53 with the Export step, chain summary, render state,
  matching-analysis state, and final sample peak.
- Keep region naming available from every step.

### PR 6: documentation and full-session verification

- Update screenshots, README instructions, and the full-session tutorial.
- Add a full-chain save, reopen, rerender, compare, and export acceptance test.

Crossfaded A/B and seek transitions remain separate work under issue #10. LUFS
and true-peak measurements remain separate work under issues #9 and #44. They can
use the consolidated comparison and measurement contracts after this milestone.

## Milestone acceptance journeys

1. Load a mix without a reference, mark a passage, skip Repair, Match, Manual EQ,
   and Dynamics, enable and adjust output gain, then export. Every disabled primary
   action has an adjacent reason, not only a tooltip.
2. Repair a passage, match to a reference, add EQ and compression, set output gain,
   and compare every stage. For each comparison, the viewer uses the named raw
   `ChainResult` prefixes, and playback equals `audition_pair(before, after)` for
   those prefixes.
3. Render the full chain, change an earlier stage, and see preserved later settings
   while each affected downstream stage reports Needs render, comparison output is
   unavailable, final measurements are absent, and Export is disabled until render.
4. Save, reopen in a fresh window, restore the same project and session context,
   rerender without relearning, and export. After reading the WAV, its samples equal
   the reopened recipe's direct render converted to `float32`, with the project
   sample rate and channel count.
5. Attempt New, Open, replacement mix, and Close with unsaved work, and retain the
   project after Cancel, a cancelled Save As dialog, or a save error. Continue the
   deferred action only after Save succeeds or Discard is confirmed.

## UX review questions

At each step, ask:

- Where am I in the workflow?
- What signal am I seeing and hearing?
- Does this result reflect my current settings?
- What will Export contain?
- What is the next available action?
