# Help test Omazone before 1.0

You don't need to be a developer or mastering engineer. Use a mix you know well
and tell us what happened. Confusing controls count as feedback. A report that
everything worked is useful too.

## Get the community beta

The planned snapshot is **0.4.0 beta 1**, spelled `0.4.0b1` in package metadata and
`v0.4.0b1` on GitHub. This is a testing release on the way to 1.0, not the finished
1.0 release.

[Download the beta](https://github.com/marrabld/omazone/releases/tag/v0.4.0b1)
once published. If that page is unavailable, it is not published yet. Use this
numbered snapshot for testing rather than the changing development build.

For reports, copy the version from your downloaded filename/release page. Its
`BUILD-INFO` download also records the version and exact source commit.

- **Windows x64:** extract the zip and run `Omazone.exe`. Keep `_internal` beside it.
- **Apple Silicon, macOS 14+:** extract and move `Omazone.app` to Applications.
  Intel Macs are not supported by this bundle.
- **Arch / Omarchy x86-64:** install with `sudo pacman -U` followed by the downloaded
  `.pkg.tar.zst` filename.

Windows and Mac builds are unsigned. See [platform notes](packaging.md) for
first-launch warnings and checksum verification. Only approve software you
intended to download; don't disable security settings globally. Other Linux
systems can [run from source](../README.md#from-source), but have no supported
binary in this beta.

## Before you start

- Work with copies of recordings and back up existing projects. Save beta projects
  under new names. Never overwrite your only mix or master.
- Start at a low listening level, especially when raising gain or testing limiting.
- Keep source recordings: a saved project is a recipe, not an audio archive.
- You don't need to upload copyrighted music, private recordings or full projects.
  Describe the problem first; share a short, permitted example only if needed.

## A first session: about 20–30 minutes

Do as much or as little as you can and say which parts you tested. Mono, stereo,
different genres and ordinary laptop screens are all welcome.

1. **Open and play.** Load your mix and a reference. Select your intended output
   device. Try playback, seeking and looping. Note dropouts, wrong-device playback,
   unexpected level jumps or confusing steps. Audition stays at source rate;
   the reference can have a different sample rate.
2. **Try ordinary matching.** Render the whole song, adjust the amount, and compare
   before/after at matched loudness. Listen to quiet and busy passages. Does
   switching work without losing your place? Does the change help your mix?
3. **Save and reopen.** Make an EQ or dynamics adjustment, render, save under a new
   project name, close and reopen. Check your settings, then render the saved
   recipe and listen again. It should not silently relearn matching or change choices.
4. **Deliver a file.** Export 32-bit float at source rate, then 24-bit PCM with
   dither at 44.1 or 48 kHz. Read safety prompts rather than dismissing them.
   Reopen the export in another audio app: check rate, channels, length and sound.
   If you have a separate LUFS/true-peak meter, record its name and readings;
   ordinary players may not measure these.
5. **Tell us where you got stuck.** Include misunderstood labels, clipped text,
   unreadable plots or actions you couldn't find. Screenshots and display
   size/scaling help. You don't need to suggest a fix.

## Two optional focused comparisons

### Ordinary versus range-aware matching

Keep the same mix, reference and amount/smoothing/limits. Try ordinary matching,
then range-aware matching on the same passages at matched loudness. Record your
preference, neither, or no clear difference, with timestamps/settings if possible.
The envelope describes measured reference variation, not a quality target; output
is not promised to fit inside it. Range-aware matching is not automatically better.
See [the guide](range-aware-matching.md).

### Loudness targeting and limiting

In Output, expand **Loudness target...**, choose your own target and ceiling,
then **Calculate proposal**. Calculation should leave audio unchanged. Inspect
gain-only mode first: a peak-constrained shortfall is expected, not necessarily
a bug. If you want limiting, select it explicitly and recalculate.

Only **Apply proposal and render** changes output settings. Compare requested
and measured delivery-rate results, then export and inspect **Last written file**.
Listen for pumping, damaged transients or unexpected level changes. Note settings
and timestamps. No LUFS number is a universal streaming requirement. See
[the targeting guide](loudness-targeting.md).

## Send a report

[Open a beta feedback report](https://github.com/marrabld/omazone/issues/new?template=beta-feedback.yml).
One distinct problem per report helps follow-up. Successful sessions can use one
report for the checklist. Without GitHub, send the same details to the maintainer
in the community thread where you found the beta.

```text
Version/build: 0.4.0b1 (or the exact different version you ran)
OS and version:
Audio device and backend shown in Omazone:
Display resolution/scaling (for layout problems):
Input: mono/stereo, sample rate, approximate length (no upload needed)
What I tried (steps and settings):
What I expected:
What happened (or which checks worked):
Can I repeat it? Always / sometimes / once
Export format/rate and external meter/readings, if relevant:
Screenshot or short permitted example, if useful:
```

Remove personal file paths and private details from logs/screenshots. Put crashes,
lost work, wrong-device playback or unsafe exports at the start of the report.
Don't keep testing a level problem at high volume.

The maintainer will collect replies and group bugs, workflow problems and later
feature requests. Crashes, lost work, incorrect audio, unsafe delivery and broken
installation come first. We may ask for a smaller example or a retest in beta 2.
There is no fixed 1.0 date or promise that every requested feature will land.
