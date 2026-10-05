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
