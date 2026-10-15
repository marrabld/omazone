# Three-band compression

Three-band mode is an optional alternative to broadband compression in the
existing Dynamics stage. It follows matching, manual EQ and corrective filters, and precedes output
gain and the final limiter. It does not add another navigation step.

![Three-band crossover controls, selected-band detector and reduction history](images/omazone-multiband.png)

## Workflow

1. Open **Dynamics**, choose **Three-band compression**, and check **Enable
   compressor in the chain**. Old projects continue to use broadband mode.
2. Drag the two crossover lines in the frequency graph, or expand **Crossover
   frequencies** to enter their values. The initial boundaries are 250 Hz and
   2.5 kHz at normal recording rates.
3. Choose **Low**, **Mid** or **High**, then adjust that band's threshold, ratio,
   makeup gain and advanced timing, knee and detector settings.
4. The three bands start neutral at **1:1**, with zero makeup. Increase a selected
   ratio to start compressing. Bypassing a band skips its compression and makeup
   gain; it does not mute that part of the song.
5. Click **Render compression**. The whole song is processed, not just a selected
   passage. The animated activity bar and elapsed time remain visible.
6. Compare before/after the entire Dynamics stage. The selected band's detector
   and threshold are shown above all three gain-reduction traces.
7. **Solo selected band** listens to that rendered band only. Exit solo to restore
   full-stage A/B. Solo is not an export setting: export always includes all three
   bands and the later output stages.
8. Review final peaks in Output. Band processing can introduce new peaks, so the
   final limiter is still a separate, optional safety choice.

Switching between broadband and three-band mode retains both sets of compressor
settings. Selecting a band or using solo does not rerender or change the processing
recipe. Changing a crossover or compressor setting invalidates Dynamics and later
output, retaining earlier matching calibration and EQ.

## Crossover design and phase

The prototype uses two Kaiser-windowed low-pass FIR filters of equal length:

```text
Low  = low-pass at the lower boundary
Mid  = low-pass at the upper boundary - Low
High = delayed original - low-pass at the upper boundary
```

Their sum is the delayed original, including phase, not just a flat magnitude
response. The default FIR length is 2049 taps, giving a delay of 1024 samples.
That is about 21.3 ms at 48 kHz or 64 ms at 16 kHz. Filter length is stored in the
recipe; this first inspector does not expose arbitrary slopes or filter designs.

File rendering feeds the required delayed tail, removes the leading delay and
keeps exactly the original number of samples. Diagnostic timestamps and solo
audio use that compensated timeline. Unity gain in every band, or bypassing all
three band compressors, reconstructs the input within **1e-12 absolute sample
error** on the test signals.

The FIR responses are linear phase and share a delay. When different band gains
are applied, cancellation of their individual ringing is incomplete. Pre-ringing
and softened attacks are possible, especially with strong differences between
neighbouring gains. This is a measured, conventional crossover prototype, not
FabFilter's Dynamic Phase algorithm. The separate future investigation is #83.

## Detectors and audition

Each filtered band has its own causal peak/RMS detector, attack/release envelope,
soft knee and manual makeup. It reuses the broadband compressor's gain law.
Within a band, the same gain controls both channels, preserving stereo linking.
There is no automatic makeup or implicit final limiting.

Solo uses the processed band, after its own compression or bypass. Its raw
waveform remains distinct from preview-only headroom attenuation. Full-stage A/B
uses the existing integrated-loudness matching. Solo has no whole-mix loudness
target; only headroom is applied when necessary.

Band-solo buffers are float32 and prepared in the worker. Export uses the full
float64 mixed render. These buffers cost roughly **414 MB extra** for a six-minute
48 kHz stereo song. A band exceeding preview headroom needs an additional scaled
copy. This is an offline prototype; investigate memory further before expanding
band count. Solo itself is not restored as a processing choice when reopening a
project.

## Verification

Tests cover complex frequency-response reconstruction, crossover boundaries,
impulses, noise, stereo linking, reset, irregular block sizes, short files and
endpoint retention. Independent band processing is compared with neutral bands.
GUI checks cover crossover dragging, per-band editing, mode-preset retention,
solo signal/labels, full-mix export while soloed, saved recipes and upstream cache
reuse. The shared compressor optimisation is checked against its earlier scalar
detector recurrence.

Use real bass-heavy passages, dense mixes and wide stereo recordings to check
for pumping, pre-ringing, changed attacks and stereo behaviour. Compare against
broadband compression and fixed EQ before adding features. No selective-range
dynamic filtering, expansion, extra bands or plugin integration is included here.
