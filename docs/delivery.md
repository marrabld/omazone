# Delivery formats, headroom and dither

Export writes the full rendered chain, never a selected passage, loudness-matched
preview or solo band. The format and dither choices only affect the written file.
They do not change the saved processing recipe or what A/B playback auditions.

## Choose a format

| Choice | File subtype | Use it for |
| --- | --- | --- |
| 32-bit float WAV | `FLOAT` | Keeping a master for further processing. The default. |
| 24-bit PCM WAV | `PCM_24` | Delivery to a service or another person that accepts 24-bit WAV. |

16-bit export is deferred. Both choices keep the recording's sample rate, channel
layout and sample count. There is no resampling or lossy encoding here. Check
the recipient's requirements rather than assuming one format fits every upload.

Float files can preserve samples above 0 dBFS so they can be lowered later, but
playback devices may still clip them. Float is not a guarantee of safe playback,
and writing a file cannot repair clipping already present in the source. A
32-bit float file also has less precision than the internal float64 render.

## Check headroom before writing PCM

24-bit PCM has a fixed range. Export checks the highest absolute sample before
writing and reserves the small amount of additional headroom required by dither.
If the file would not fit, it asks you to choose between:

- **Attenuate and export.** Apply the displayed reduction to this file only.
- **Cancel.** Review Output, lower output gain or adjust the limiter, then render
  again so you can audition that change before exporting.

The reduction is rounded up to hundredths of a dB. Even a tiny excess gets an
actionable `0.01 dB` suggestion, never `attenuate 0.00 dB`. Cancel is the dialog's
default and Escape action. Nothing is clipped or normalised silently.

An export-only reduction makes a quieter file but does not update audition,
output gain, compression, the limiter or the saved recipe. The completion message
states the reduction and the actual written sample peak.

The check concerns sample representation, not reconstructed true peaks. A PCM
file that fits can still exceed 0 dBTP between samples. Export keeps the final
LUFS and true-peak readings visible; review Output and the optional limiter if
those readings warn. A ceiling around -1 dBTP is a common starting point for
streaming, not a universal upload requirement or a guarantee about later lossy
encoding. See [true peak](true-peak.md) and [limiting](limiter.md).

## Dither

Reducing the floating-point render to 24-bit PCM puts samples on a fixed grid.
Without dither, this quantisation error depends on the signal and can produce
distortion in very quiet material. At 24-bit its level is already very low;
we do not claim everyone will hear the difference.

**Apply dither** defaults on for 24-bit and is disabled for float. We add standard
TPDF noise with a triangular distribution, one quantisation step peak in either
direction, before rounding to the nearest PCM code. That is two LSBs
peak-to-peak. The noise amplitude stays fixed throughout the file. Export refuses
insufficient headroom rather than reducing or silently skipping the dither.

Quantisation happens once, with explicit nearest-even rounding. Integer codes
are packed into the WAV without another floating-point quantisation pass.
Tests on decoded files check the error's mean and variance, including signals
between quantisation levels and both channels. For dithered digital silence,
the resulting RMS level is approximately -144.5 dBFS in normalised sample units.

Dither once, at the final conversion to integer PCM. If more processing or
sample-rate conversion is planned, prefer float and let the final tool handle
the integer conversion and dither. The checkbox can be turned off deliberately.
Export adds no noise shaping, loudness target or automatic gain boost.

Unseeded exports use fresh random noise, so separate dithered exports can differ
at the sample level. Tests supply a seed for repeatable comparisons. Format
choices persist for repeated exports in the current project but reset to float
with dither enabled for integers when opening a project or loading a new source.
They are not stored as processing settings in the project recipe.

![Export with 24-bit selected, dither enabled and a fitting sample peak](images/omazone-delivery.png)

Regenerate with `uv run python tools/render_delivery_demo.py docs/images`.
The fixture uses generated tones and seeded noise, not a private recording.

## Verification and file safety

Run `uv run pytest tests/test_delivery.py tests/test_export_delivery.py -q`.

The tests read actual WAV files back, checking PCM codes against an independent
rounding reference, unbiased dithered error, variance across sub-LSB offsets,
full-amplitude dither near the ceiling, silence, channel layout, frame count,
rate, seeded repeatability and float headroom. They also exercise the real Qt
confirmation dialog, both answers, stale-render guards, saved-project resets,
cached peak display and full-chain export while a band is soloed.

Conversion uses bounded blocks instead of allocating several extra full-song
buffers. A temporary file in the destination directory is atomically renamed
only after a successful write. A failed conversion or write leaves an existing
destination untouched and removes the temporary file. Validation completes
before output is opened.

Saving the repaired source is separate and remains 64-bit float WAV. No delivery
conversion, attenuation or dither is applied to that archival export.
