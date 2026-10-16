# Maintainer checklist: community beta to 1.0

This is the procedure, not a request to publish automatically. The first snapshot
is **v0.4.0b1** (0.4.0 beta 1). A 0.x beta does not prevent a later 1.0; it
identifies this particular testing snapshot.

## Prepare and approve

- Review the PR: version/lockfile, prerelease publication, platform metadata,
  testing guide, release notes and announcement.
- Confirm local tests, lint and bundled self-test pass. Review PR build checks.
  Automated tests do not replace the maintainer's listening.
- Merge when ready. A merge updates development downloads after successful
  Windows, Mac and Arch jobs; it does **not** create the numbered beta.
- Confirm the merged commit's coordinated **Desktop release and publish** run
  passed all platforms. Fix failures before creating the snapshot.

## Publish the immutable beta

The maintainer or authorised helper does this after explicit approval:

1. Fetch `origin/main` and identify the approved commit. Check that its project
   version is `0.4.0b1` and no `v0.4.0b1` tag exists already.
2. Create an annotated `v0.4.0b1` tag on that commit and push **only that tag**.
   Never move a numbered tag to repair a release; use beta 2 instead.
3. Watch **Desktop release and publish** for the tag. It rebuilds/self-tests all
   three bundles and verifies version/commit agreement before publication.
4. Confirm the release is **Pre-release**, not Latest. The previous stable release
   must remain Latest. Check all three downloads and SHA256SUMS. The body should
   include `docs/releases/v0.4.0b1.md` plus generated verification details.
   Don't announce a failed build as published.
5. Download the published bundle for a platform you have and do a short
   launch/play/save/export check. Nobody has to own every supported system.
6. Post [the announcement](beta-announcement.md) and [testing guide](beta-testing.md).

Don't create a public release manually before the build: published numbered
releases are immutable to the publisher. On failure, inspect the job and any
draft before deciding whether a same-tag retry is safe.

## Collect and triage

- Accept GitHub reports and community replies. Record version, OS, device/backend,
  steps and outcome. Ask before copying private material publicly.
- Group duplicates, link originals and separate bugs from feature requests.
  Record successful tests too, especially real-device playback.
- Fix crashes, lost work, incorrect processing, unsafe delivery and installation
  failures first. Next address blocked/misleading workflows and unreadable layouts.
  Cosmetic requests and new modules need not block 1.0.
- Use #100 for ordinary/range-aware comparisons, #102 for loudness results, #14
  for playback and #70 for layout evidence. Link findings rather than closing
  these solely because code merged. Existing maintainer tests count; ask for
  missing specifics rather than repeating all work.
- Reproduce where possible, add regression coverage, request a retest and note
  which beta contains each fix. Don't call an unreproduced problem fixed.
- Publish `0.4.0b2` if needed, with a new version PR/tag and release notes.
  Keep beta 1 available so reports remain traceable.

## Decide whether 1.0 is ready

Revisit [the roadmap's acceptance criteria](../roadmap.md). Record an end-to-end
load/edit/compare/save/reopen/deliver session on the candidate, including selected
conversion, loudness/peak constraints and actual written-file measurements.
Include real-music listening, not only numeric checks.

Resolve known release-blocking failures in the supported core workflow, document
remaining limitations and confirm platform builds pass. Don't promise every
feature request or wait indefinitely for unavailable hardware. Describe platforms
without real-device reports honestly. Deferred #79, #83 and #88 remain separate
scope unless the maintainer explicitly changes that decision.

When ready, prepare a separate `1.0.0` version/release PR, review it, then tag the
approved commit. The stable tag may become Latest; the beta must not.
