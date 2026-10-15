# Delivery formats, headroom and dither

The Export step writes the full rendered chain. This page explains what each
file type is for, why an integer file needs headroom, and what dither does.
It is written for people who have not had to think about bit depth before.

## The three formats

| Choice | File | Use it for |
| --- | --- | --- |
| 32-bit float WAV | `FLOAT` | Keeping the master. The default. |
| 24-bit WAV | `PCM_24` | Handing a file to a mastering engineer, a distributor, or a streaming upload. |
| 16-bit WAV | `PCM_16` | Only if something specifically asks for CD quality. |

The render is floating point internally, so nothing is lost while you work. The
format choice only changes the file written at the end. Nothing about audition
changes: A/B playback and the saved project always use the full-precision
render, so what you hear is what the recipe produced.

Float is the default because it has no ceiling. A float file can hold samples
above 0 dBFS without damage, so you can write a master now and still attenuate
it later. Integer files cannot: anything above their ceiling is destroyed
permanently.

## Why 24-bit needs headroom

An integer WAV stores fixed numbers. At 24-bit the highest value it can hold is
just under 1.0, which is 0 dBFS. A value above that does not have a
representation, and the file writer clamps it. That clamping is clipping, and it
is permanent and audible.

Omazone will not do that to your file silently. If the render peaks above the
format's ceiling, Export asks before writing:

> The rendered peak is +2.14 dBFS, above the ceiling of 24-bit WAV. Writing it
> directly would clip the peaks.

You can then attenuate by the stated amount and export, or cancel and lower
output gain or the limiter ceiling instead. There is no hidden normalisation
anywhere in this app: nothing changes your level unless you choose to change it.

### Working practice

Most engineers want your master peaking between about -1 and -3 dBFS, and
streaming platforms typically expect a true peak around -1 dBTP. The optional
final limiter can set that ceiling for you, and the Output step shows the
resulting reconstructed peak. See [true peak](true-peak.md) and the
[limiter](limiter.md).

If you skip the limiter, watch the "Final export" reading in Output. Anything
above -1 dBFS will need attenuation before a 24-bit write.

## What dither does

Reducing to 24 or 16 bits rounds every sample to the nearest representable
value. That rounding is itself a distortion, and on a quiet passage it can be
heard as distortion rather than as a limit.

Dither adds a very small amount of noise before rounding. The noise pushes each
sample across a rounding boundary at a random moment, which turns the
distortion into a steady, gentle noise floor instead. The trade is a noise
floor of about -66 dBFS in exchange for removing audible distortion. Almost
nobody can hear -66 dBFS of noise, and many people can hear quantisation.

Omazone applies **TPDF** (triangular probability density function) dither, which
uses two independent uniform random values summed together. Rectangular dither,
from a single random value, is measurably worse at the same level.

Dither is **on by default for 24 and 16-bit**, and **never applied to float**.
That matches professional practice: Ozone's own documentation says to dither
only when reducing from a higher bit depth, and to use it when bouncing a 24-bit
master.

Two rules that matter:

- Dither **once, last**. If a later tool will requantise your file, export
  float and let the final tool dither. Dithering twice adds two noise floors.
- Dither changes noise, not level. It is not a way to make a file louder or
  quieter, and it is not normalisation.

If you have somewhere else that will handle the conversion, switch dither off
here and export float.

![Export step with the delivery format panel showing that the current peak fits 24-bit with dither](images/omazone-delivery.png)

Regenerate with `uv run python tools/render_delivery_demo.py docs/images`. The
fixture is generated noise and tones, not a private recording.

## Verification

Run `uv run pytest tests/test_delivery.py tests/test_export_delivery.py -q`.

Tests cover float headroom, refusal and measured attenuation for both integer
depths, positive and negative overload, quantisation error bounded by one step,
dither keeping a full-scale peak below the ceiling, silence, low-level tones,
mono and multi-channel layout, written subtype and frame count, deterministic
seeded dither, the Export panel's stated headroom, the confirmation dialog in
both branches, and that export always writes the full chain while auditioning a
band solo.

Dither is stochastic. Tests that assert exact samples pass a fixed seed; the
application leaves the seed unset so every export is an independent draw. Two
exports of the same master are therefore not bit-identical when dither is on.
That is expected and is the same for any dithering tool.

## Limits

There is no sample-rate conversion, MP3 or AAC export, lossy codec, or
streaming-service preset here. Export writes WAV at the recording's own sample
rate. If a distributor asks for a different rate, use a converter you trust and
tell it the file is already dithered, or export float and let that tool handle
both.

Saving the repaired source is a separate action and is unaffected. It stays
64-bit float so the internal samples survive.