# Contributing to Omazone

Choose an [open issue](https://github.com/marrabld/omazone/issues) and comment with
your proposed approach. The [roadmap](roadmap.md) describes the processing goals.
Small DSP experiments, listening reports, documentation, and interface work are welcome.

## Branch and pull request workflow

Use a feature branch for each focused change. Start from an up-to-date `main`:

```bash
git switch main
git pull --ff-only
git switch -c feature/short-description
uv sync --python 3.13
```

Implement the change, update relevant docs, and run:

```bash
uv run ruff check .
uv run pytest
```

Commit the intended files, push the branch to your fork or this repository if you
have write access, and open a pull request against `main`. Include the linked
issue, verification results, screenshots for UI changes, and measurements or
listening observations for DSP changes. GitHub Actions runs lint and tests on PRs.

Ask for review before merging. Use a draft PR for experiments that are not ready.
If a feature depends on another unmerged PR, base it on that feature branch and
identify the dependency in its description. Merge the prerequisite first, then
retarget the dependent PR to `main` and review its final diff.

## DSP contributions

- Keep numerical processing independent of Qt and audio devices.
- Explain sample/channel layout, state, latency, and tail handling.
- Verify with generated signals or small examples that can be redistributed.
- For stateful processors, check irregular block sizes, reset, and stereo behaviour.
- Report algorithms' failure cases as well as improvements. A mathematical match
  does not establish that processing sounds better.

Do not commit private recordings or commercial reference tracks. Contributions
use the project's [GPLv3 license](LICENSE).

## Desktop builds and publication

Merging a PR into `main` automatically starts **Desktop release and publish**. It
builds Windows x64, an Apple Silicon macOS app and an Arch Linux package, runs each
bundled application's self-test, and publishes all three together. There is no
separate publishing button or routine version-tag step.

Publication is coordinated. Individual platform workflows only upload artifacts.
The release step refuses to publish unless all three archives exist, are
non-empty, and report the release version and commit, so a broken or stale
platform leaves the last public downloads in place instead of half-updating them.
A queued build whose source is no longer the current `main` skips publishing, so an
old rerun cannot replace newer code. Failed builds never publish.

PRs that change app or packaging files exercise the Windows build without
publishing. A manual run of any platform workflow produces temporary artifacts
only. The Windows workflow serialises runs for the same ref.

See [platform builds](docs/packaging.md) for what each bundle contains, how the
Mac and Arch packages are verified, and the current signing and validation limits.

For a deliberate numbered snapshot, run `uv version 0.2.0 --no-sync` (choose
the intended version), review the `pyproject.toml` and `uv.lock` changes, and
merge that version-bump PR into `main`. Push a matching `v0.2.0` tag on the new
main commit. The tag workflow creates the numbered GitHub release and uploads
every platform archive, one shared checksum file and per-platform build
metadata. Do not create the tag or release before the source version matches: a
mismatched tag fails before packaging. The workflow can fill an existing empty
numbered release, but never replaces assets on an already-published numbered
build. The workflow-owned lightweight `development` tag advances only by a
non-forced fast-forward, and each bundle records its full source commit. A
per-merge version bump is not needed for the rolling `development` download.
