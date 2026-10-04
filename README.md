# Omazone

A Python desktop playground for learning audio signal processing and building
useful mastering tools. Load a mix and a reference, inspect their spectra, design
a matching EQ, then listen to the result with level-matched A/B playback.

The aim is to make the processing understandable: show what the algorithm
measures, what correction it requests, and what the resulting filter actually
does. This is an early, working prototype. Contributions from musicians, DSP
developers, and people who enjoy building audio interfaces are welcome.

![Omazone showing mix, reference, and processed spectra, requested and actual EQ responses, and the A/B playback scrubber](docs/images/omazone-spectral-matching.png)

## What works now

- Reference-matching EQ with adjustable amount, octave smoothing, and boost/cut limits.
- Mix, reference, and processed spectrum plots.
- Requested correction versus actual linear-phase FIR response.
- Offline, block-based rendering with compensated filter delay.
- RMS-matched original/processed A/B playback at the same song position.
- Click-and-drag seeking, elapsed/total time, and pause/resume.
- Linked mono/stereo waveform views with peak-preserving zoom and region selection.
- One-shot selection playback and sample-aligned loops with shared original/processed A/B.
- Named targets captured from reference passages, with JSON profile save/load.
- Per-section matching settings and full-song rendering with aligned EQ transitions.
- Selected-region clipping inspection with editable rails, channel diagnostics, and waveform markers.
- Checked-candidate short-gap declipping, original/repaired A/B, reset, and full-precision repair export.
- Mono/stereo WAV, FLAC, and AIFF input; references can have a different sample rate.
- 32-bit floating-point WAV export.

Filtering, compression, dynamic EQ, and declipping are planned. See the
[roadmap](roadmap.md) for the proposed modules and places to contribute.

## Quick start

You need Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).
The setup below uses Python 3.13, which uv can download if needed.

```bash
git clone https://github.com/marrabld/omazone.git
cd omazone
uv sync --python 3.13
uv run omazone
```

For an existing checkout:

```bash
uv sync --python 3.13
uv run omazone
```

The desktop app has been exercised on Linux. Qt supplies cross-platform GUI
support, but Windows and macOS installation and playback still need validation.
Playback uses the default audio output device through PortAudio. If a platform
reports a missing PortAudio library, install it using that system's package manager.

## Try it on a song

1. Load a mix and a broadly similar reference track.
2. Start with 50% amount, 0.33-octave smoothing, and 6 dB boost/cut limits.
3. Click **Analyse + process** to render the result.
4. Click **Play**, then use the listening button to switch original/processed.
5. Seek to a verse, chorus, or transient-heavy section and compare again.
6. Export when you want to use the processed file elsewhere.

Play auditions the original initially; the listening button switches to the
processed version at the same playback position. Click or drag the seek bar to
audition another section. Playback pauses during a drag and resumes on release
if it was playing. Play/Pause and Stop preserve the cursor; seek to the left edge
to restart. Pressing Play after reaching the end starts from the beginning.
The time display shows elapsed and total time. With the seek bar focused, arrow
keys move one second and Page Up/Down move ten seconds.
Changed settings require another render. WAV, FLAC, and AIFF mono/stereo files
are supported. Different reference and mix sample rates are supported.

## Inspect a waveform and select a region

Loading a mix opens the **Waveform / selection** tab. It shows the original mix,
with separate linked plots for left and right channels, or one plot for mono.
The existing spectrum and correction plots are under **Spectrum / EQ**.

![Omazone showing a full-song stereo waveform, selected region, exact sample bounds, and the A/B playback cursor](docs/images/omazone-waveform-selection.png)

- Use the mouse wheel to zoom horizontally and drag the background to pan.
- **Shift+drag** on the background creates a selection. Drag its green edges to
  resize it, or drag the shaded region to move it. Both channels share the region.
- Edit **Start** and **End** in seconds for precise adjustment. Spin-box steps
  move one sample; the label shows exact sample indices and duration.
- **Select view** selects the visible time range. **Zoom selection** magnifies
  the region; **Fit song** returns to the full track; **Clear** removes it.
- Click the background to seek, or use **Seek start** to audition from the selected
  region's beginning. The white cursor follows the existing playback transport.

Selection bounds are half-open sample intervals `[start, end)`, like a NumPy
slice. Processing and export still use the whole file.
A new mix clears the selection, while changing matching controls or the reference
keeps it.

### Play and loop a selection

![Omazone looping a selected stereo passage and switching from original to processed while preserving the playback position](docs/images/omazone-loop-demo.gif)

The silent demo uses generated audio and the actual playback callback with a
simulated device. Watch the white cursor wrap at the selection's end and the
listening button switch to processed. [Recreate the demo](docs/loop-demo.md).

Select a passage, then click **Play selection**. Playback starts at its beginning
and stops at its exclusive end. Enable **Loop selection** to repeat it instead.
The loop toggle prepares selected-region playback; if playback is already running,
it continues within the selected bounds. Turning looping off retains one-shot
selection mode, so playback stops at the region end.

- A/B switches keep the same sample position, including across loop boundaries.
- Play/Pause and Stop preserve the cursor and playback mode. Play after the end
  of a one-shot selection restarts at its beginning.
- Seeking inside the active region retains its mode. Seeking outside it returns
  to whole-song mode and turns looping off. **Whole song** also exits region mode.
- Editing an active selection pauses playback, then resumes after the bounds
  settle. Clearing it returns to whole-song mode; Stop cancels a pending resume.
- A new mix resets region playback and looping.

Loop wraps are sample-exact, including regions shorter than an audio callback.
There are no boundary fades yet, so mismatched endpoints can produce clicks;
choose musically sensible boundaries while transition smoothing is developed.

Wide views draw min/max buckets to preserve transients. Zooming in displays raw
samples. An overview bucket may include samples just outside the viewport edge;
zoom to sample level when inspecting an exact boundary. Peak-index construction
runs alongside file analysis in the loading worker, while redraws use a bounded
number of display points.

## Match metal and clean sections separately

Use different targets when a song contains passages with different tonal goals.
Each mix section is analysed independently against its assigned target; the
whole-song spectrum is not used to design its correction.

![Two mix sections assigned to separate Metal and Clean spectral targets](docs/images/omazone-section-matching.png)

1. Load a reference and open **Reference targets**. Shift+drag its metal passage,
   enter a target name such as `Metal`, and click **Capture target**.
2. Select its clean passage and capture `Clean`. You can also capture targets
   from different reference files; earlier targets remain in the library.
3. Load your mix. In **Waveform / selection**, select the mix's metal passage.
4. Open **Mix sections**, click **Use mix selection**, name the section, choose
   `Metal`, set its amount/smoothing/gain limits, and click **Add section**.
5. Repeat for the clean passage with the `Clean` target. Assignments cannot overlap.
6. Choose a **Transition** duration and click **Render sections**. This produces
   one complete preview and exportable file, keeping the original duration.
7. Select a table row and use **Inspect EQ** to view its requested and actual
   correction. **Audition section** uses the existing transport; enable
   **Loop selection** and switch original/processed to compare it repeatedly.

To change an assignment, select its row, edit the fields, and click **Apply to
selected**. **Inspect EQ** and **Render sections** also apply pending edits to the
selected row. Edits invalidate the old preview and export until you render again.
The controls below the tabs and **Analyse + process** still perform whole-song
matching; section settings live in the section table and editor.

Coloured named regions appear on the mix waveform. Dashed yellow windows show
where neighbouring filtered signals, or a filter and the original audio, blend.
Unassigned samples outside these windows stay unchanged.

The transition setting is the requested full window duration, centred on each
boundary. Windows shorten automatically when neighbouring sections or gaps are
small, so they cannot overlap. The summary lists their effective durations.
At file edges no transition is added. Setting the duration to zero gives a hard
switch; even a nonzero fade can produce an audible tonal change.

Rendering uses surrounding audio context for each linear-phase FIR, compensates
delay before mixing paths, and uses complementary raised-cosine amplitude weights.
Identical paths therefore do not receive an equal-power crossfade gain boost.

**Save targets / Load targets** store versioned JSON spectral profiles and passage
metadata, not the recordings. Imports merge into the library; colliding IDs become
new targets so existing assignments are preserved. References may use a different
sample rate from the mix. Choose at least 0.1 seconds of non-silent material;
longer representative passages generally make better tonal targets.

In this first version, section assignments remain in memory and must be recreated
after restarting the app. Loading a new mix clears them but retains captured
targets. Preview level matching uses whole-file RMS, not separate per-section
loudness normalisation. Section matching controls tonal balance, not dynamics or
vocal/instrument balance.

## Inspect suspected clipping in a selected passage

Select the affected passage in **Waveform / selection**, then open **Clipping
inspection** and click **Analyse selection**. Analysis reads the original loaded
audio only; it does not alter samples, an existing render, or exported audio.

![Clipping inspection showing separate positive and negative rails, per-channel statistics, and suspected intervals](docs/images/omazone-clipping-inspection.png)

- **Channel** scans both stereo channels or a single channel. Mono files have one option.
- **+ threshold / - threshold** are independent linear sample amplitudes, not dB.
  Defaults are +1 and -1; scaled-down recordings may have lower clipping levels.
- **Tolerance** controls how close samples must be to a rail and how little
  variation is allowed across a plateau. The default 0.00005 includes common
  near-full-scale PCM rails. **Min run** excludes isolated threshold hits.
- **Suggest levels** looks for high-amplitude flat-run evidence. It leaves a
  polarity unchanged when it finds no evidence. If stereo levels disagree,
  select Left/Right and inspect them separately rather than assuming a common rail.
- Select a table row and click **Show interval**, or double-click it, to zoom
  into its samples and seek there. **Show selection** fits the analysed passage.
- Changing settings, changing the mix selection, or loading a new mix clears
  stale diagnostics. **Clear markers** removes the results without changing audio.

![Red suspected-plateau markers on a clipped left channel and an orange over-range marker on the right](docs/images/omazone-clipping-markers.png)

Red markers identify suspected flat threshold runs, with dashed red lines showing
the chosen rails. Orange markers identify samples above full scale. The screenshots
use generated stereo audio to illustrate the distinction: the left signal was
deliberately hard-clipped below full scale, while the right contains an over-range
floating-point passage.

An over-range float sample can retain its original waveform; reducing gain before
playback or conversion may address that overload. A flattened recorded peak has
lost information even after it is turned down. Neither condition is diagnosed
solely by the other one's marker.

Results use half-open absolute sample bounds. The table shows up to 500 intervals;
counts include all detected intervals. Wide waveform views thin dense markers to
keep redraws bounded; zoom in to inspect individual runs.

This is a heuristic, not proof of clipping. Clean low-frequency extrema, quantised
or synthesised waveforms can look flat. Very short damage, soft/analogue distortion,
or a clipped instrument mixed with other sources can evade detection. Absence of
markers does not mean the recording is clean. For the acoustic-guitar repair,
analyse the isolated recording or stem when available. Nearly constant selections
provide insufficient waveform context and are not marked as clipping.

Detection candidates are not automatically applied as a repair mask. Check the
intervals you want to try reconstructing using the workflow below.

## Reconstruct short clipped peaks

In **Clipping inspection**, analyse a passage and inspect its candidates, then:

1. Check individual **Repair?** boxes, or **Check shown** to include the displayed
   plateau candidates. Over-range-only rows are not eligible.
2. Set **Max gap**, **Context / side**, and **Peak bound**. Defaults are 1 ms,
   eight intact samples per side, and four times the applicable clipping rail.
3. Click **Repair checked**. The result column reports repaired intervals and
   skipped intervals with reasons; hover over an elided reason to read it.
4. The waveform shows the original signal with a green reconstruction overlay.
   Use **Original / repaired** audition mode, looping, and the listening button
   to compare the same passage at RMS-matched levels.
5. **Export repaired WAV** saves the raw repaired signal as 64-bit floating-point
   WAV, retaining sample precision outside the repaired mask. It does not include
   preview gain matching or subsequent matching EQ.
6. **Reset repair** restores the original processing input and invalidates any
   mastering render based on the repair.

![Checked repair candidates with reconstruction controls and repaired/skipped results](docs/images/omazone-declipping-controls.png)

![Green reconstructed peaks over the original flattened waveform](docs/images/omazone-declipping-waveform.png)

The original recording stays in memory unchanged. Whole-song matching and section
matching use the repaired input after a successful repair, and their previous
renders become stale. Mastering audition uses **Input / mastered**; when repair is
active, its input side is the repaired signal. **Original / repaired** remains a
separate comparison. Standard mastering export stays 32-bit float.

Repair is an offline cubic-Hermite baseline using the intact endpoint samples and
slopes fitted to surrounding audio. Only checked intervals that pass the checks
are changed, without a broad section crossfade. Long runs, insufficient or damaged
context, unsupported slopes, and inconsistent or excessive reconstructions are
skipped. Samples outside successful intervals remain exactly unchanged internally.
Restored peaks may exceed full scale; repair export preserves them rather than
silently limiting or normalising.

Each successful **Repair checked** creates a fresh result from the original and
replaces the previous repair, rather than accumulating repairs from different
selections. If no interval passes, the previous repair remains active. The table
shows at most 500 intervals, so **Check shown** covers only displayed candidates.
Use a short representative passage for this first version. Loading a new mix
clears repair state; repair sessions are not yet saved across app restarts.

This does not guarantee recovery. Tests show improvement on deliberately clipped
sine/harmonic examples, but an intentionally flat-topped waveform can be made worse.
Compare by listening, especially on guitar attacks, and use the isolated guitar
recording when available. See [the algorithm and experiment](docs/declipping.md).

## Current limitations

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
- `src/omazone/waveform.py`: peak index and GUI-independent sample-region model.
- `src/omazone/waveform_view.py`: linked waveform plots, selection, zoom, and seeking.
- `src/omazone/playback.py`: GUI-independent sample cursor and selected-region buffer filling.
- `src/omazone/sections.py`: named profiles, validation, transition planning, and contextual rendering.
- `src/omazone/section_view.py`: reference capture, profile library, and section-assignment editor.
- `src/omazone/clipping.py`: per-channel plateau candidates, over-range intervals, and threshold hints.
- `src/omazone/clipping_view.py`: diagnostics controls, results table, and interval navigation.
- `src/omazone/declipping.py`: selective Hermite reconstruction, intact-context checks, and rejected intervals.
- `tests/test_engine.py`: identity, streaming equivalence, spectral improvement,
  stereo preservation, gain limits, and preview headroom.
- `tests/test_gui.py`: render/export workflow, seeking, and shared A/B cursor.
- `tests/test_waveform.py`: preserved peaks, raw-sample zoom, and region bounds.
- `tests/test_playback.py`: exact loop wraps, short regions, one-shot endings, and A/B alignment.
- `tests/test_sections.py`: profile validation, section context, dry gaps, stereo, and transition alignment.
- `tests/test_clipping.py`: asymmetric/scaled clipping, rails, overloads, and documented detection limits.
- `tests/test_declipping.py`: repair accuracy, exact masks, rejection cases, and a known worsening case.
- `roadmap.md`: planned processing modules, priorities, and contribution ideas.

The GUI uses PySide6 and pyqtgraph. The engine uses NumPy and SciPy; SoundFile
handles audio files, and sounddevice handles preview playback. Analysis and
rendering run in a worker thread to keep the interface responsive.

## Contributing

Use feature branches and pull requests for new work. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the branch/review workflow and checks.

Start with the [roadmap](roadmap.md), then browse or open an
[issue](https://github.com/marrabld/omazone/issues). For a new processing module,
describe the intended behaviour and measurements before implementing it. Small,
focused pull requests are easiest to review.

For smaller starting tasks, try [frequency-label cleanup](https://github.com/marrabld/omazone/issues/12),
the [generated-signal tutorial](https://github.com/marrabld/omazone/issues/13), or
[Windows/macOS validation](https://github.com/marrabld/omazone/issues/14).
Comment on the issue you want to pick up so other contributors can coordinate.

Useful contributions include:

- DSP implementations with clear explanations and meaningful verification signals.
- Listening reports that identify the settings and the section being compared.
- GUI improvements, accessibility, and clearer plots.
- Windows/macOS setup and playback reports.
- Documentation and reproducible learning experiments.

To develop, fork the repository, clone your fork, run the quick-start setup, and
create a feature branch. Keep DSP code independent of the GUI and audio devices.
For stateful processing, check behaviour across different block sizes and channel
counts. Use generated signals or small redistributable examples for tests.
Audio files are ignored by default; do not commit recordings or reference tracks
unless you have permission to distribute them.

Before submitting a pull request, run:

```bash
uv run pytest
uv run ruff check .
```

Describe what changed, how you verified it, and any audible or measured tradeoffs.
Screenshots are useful for interface changes. Contributions are accepted under
the project's GPLv3 license.

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

The [roadmap](roadmap.md) explains the proposed path from this workbench to a
modular processing chain and, eventually, a real-time backend.

## License

GNU General Public License version 3 only (`GPL-3.0-only`). See [LICENSE](LICENSE)
for the full terms. You may use, modify, and redistribute Omazone under those
terms, including commercially. Distributed derivatives must remain GPL-licensed
and provide corresponding source. There is no warranty.
