"""Publish verified Windows assets with gh. PR/feature builds never publish.

The rolling development tag advances only by a non-forced fast-forward. A stale
main build is skipped instead of replacing a more recent build. Version tags are
immutable snapshots; reruns do not replace already-published versioned assets.
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def publication_target(event, ref):
    if event not in ("push", "workflow_dispatch"):
        return None
    if ref == "refs/heads/main":
        return "development"
    if event == "push" and ref.startswith("refs/tags/v"):
        return ref.removeprefix("refs/tags/")
    return None


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return result.stdout


def read_api(endpoint):
    try:
        return json.loads(gh("api", endpoint))
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return None
        raise


def read_release(repo, tag):
    return read_api(f"repos/{repo}/releases/tags/{tag}")


def publish(repo, event, ref, sha, version, assets, run_url):
    tag = publication_target(event, ref)
    if tag is None:
        return "Build complete; PR and feature-branch builds are artifacts only."
    assets = [Path(path) for path in assets]
    if not assets or any(not path.is_file() for path in assets):
        raise ValueError("Verified release assets are missing.")
    development = tag == "development"
    if development:
        main_sha = json.loads(gh("api", f"repos/{repo}/git/ref/heads/main"))["object"]["sha"]
        if main_sha != sha:
            return "Publishing skipped: main has advanced. The newer main build will publish."
    elif tag != f"v{version}":
        raise ValueError(
            f"Version tag {tag} does not match pyproject.toml version {version}. "
            "Merge a matching version bump before pushing the tag."
        )

    release = read_release(repo, tag)
    if not development and release is not None and not release["draft"] and release["assets"]:
        return f"Versioned release {tag} already exists; preserving its published assets."
    if development:
        reference = read_api(f"repos/{repo}/git/ref/tags/{tag}")
        if reference is None:
            # Draft releases do not necessarily create their tag until published.
            gh(
                "api",
                "--method",
                "POST",
                f"repos/{repo}/git/refs",
                "--raw-field",
                f"ref=refs/tags/{tag}",
                "--raw-field",
                f"sha={sha}",
            )
        elif reference["object"]["type"] != "commit":
            raise ValueError("Development tag must be a workflow-owned lightweight tag.")
    notes = (
        "This development build is updated automatically after successful main builds.\n"
        "It contains the latest changes and may be less tested than numbered releases.\n\n"
        if development
        else "Standalone Windows build from this version tag.\n\n"
    )
    notes += (
        "Download the Windows zip below, extract the entire folder, and run Omazone.exe.\n"
        "Keep the _internal folder beside the executable. Python installation is not needed.\n\n"
        f"App version: {version}\n\n"
        f"Built source: [{sha[:12]}](https://github.com/{repo}/tree/{sha})\n\n"
        f"Build and executable smoke test: {run_url}\n\n"
        "SHA256SUMS.txt contains the download checksum. BUILD-INFO.json identifies the build.\n"
        "The executable is unsigned; Windows may display a SmartScreen warning.\n"
    )
    title = "Latest development build for Windows" if development else tag
    with tempfile.TemporaryDirectory() as directory:
        notes_file = Path(directory) / "release-notes.md"
        notes_file.write_text(notes, encoding="utf-8")
        if release is None:
            # Upload into a draft first: a first release is not advertised until
            # its assets have actually uploaded successfully.
            gh(
                "release",
                "create",
                tag,
                "--repo",
                repo,
                "--target",
                sha,
                "--title",
                title,
                "--notes-file",
                str(notes_file),
                "--draft",
            )
        if development:
            # Lightweight tag owned by this workflow. Never force-push or move
            # backwards. GitHub's API rejects a non-fast-forward update.
            gh(
                "api",
                "--method",
                "PATCH",
                f"repos/{repo}/git/refs/tags/{tag}",
                "--raw-field",
                f"sha={sha}",
                "--field",
                "force=false",
            )
        upload = ["release", "upload", tag, "--repo", repo, *map(str, assets)]
        if development or release is None or release["draft"]:
            upload.append("--clobber")
        gh(*upload)
        gh(
            "release",
            "edit",
            tag,
            "--repo",
            repo,
            "--title",
            title,
            "--notes-file",
            str(notes_file),
            "--draft=false",
            f"--prerelease={'true' if development else 'false'}",
            f"--latest={'false' if development else 'true'}",
        )
    return f"Published https://github.com/{repo}/releases/tag/{tag}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "event", "ref", "sha", "version", "run-url"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--assets", nargs="+", required=True)
    args = parser.parse_args()
    print(
        publish(args.repo, args.event, args.ref, args.sha, args.version, args.assets, args.run_url)
    )


if __name__ == "__main__":
    main()
