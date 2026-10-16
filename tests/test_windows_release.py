"""Exercise publishing policy and gh operation order without changing GitHub."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "publish_windows_release.py"
spec = importlib.util.spec_from_file_location("publish_windows_release", SCRIPT)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

SHA = "a" * 40
REPO = "example/omazone"


@pytest.mark.parametrize(
    ("event", "ref", "expected"),
    [
        ("push", "refs/heads/main", "development"),
        ("workflow_dispatch", "refs/heads/main", "development"),
        ("push", "refs/tags/v0.1.1", "v0.1.1"),
        ("pull_request", "refs/pull/1/merge", None),
        ("pull_request", "refs/heads/main", None),
        ("workflow_dispatch", "refs/heads/feature/build", None),
        ("workflow_dispatch", "refs/tags/v0.1.1", None),
        ("push", "refs/tags/development", None),
    ],
)
def test_publication_policy(event, ref, expected):
    assert publisher.publication_target(event, ref) == expected


def assets(tmp_path, *, version="0.1.0", numbered=False):
    prefix = f"Omazone-{version}-" if numbered else "Omazone-"
    files = [
        tmp_path / (prefix + "windows-x64.zip"),
        tmp_path / (prefix + "macos-arm64.zip"),
        tmp_path / (prefix + "arch-x86_64.pkg.tar.zst"),
        tmp_path / "SHA256SUMS.txt",
        tmp_path / "BUILD-INFO.json",
        tmp_path / "BUILD-INFO-macos-arm64.json",
        tmp_path / "BUILD-INFO-arch-x86_64.json",
    ]
    for path in files:
        path.write_bytes(b"verified-build")
    return files


def fake_github(
    monkeypatch,
    *,
    main_sha=SHA,
    release=None,
    tag_exists=True,
    fail_upload=False,
    draft_release=None,
):
    calls = []
    notes = []

    def gh(*args):
        calls.append(args)
        if args == ("api", f"repos/{REPO}/git/ref/heads/main"):
            return json.dumps({"object": {"sha": main_sha}})
        if args[:2] == ("api", f"repos/{REPO}/git/ref/tags/development"):
            if not tag_exists:
                raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 404: Not Found")
            return json.dumps({"object": {"sha": "b" * 40, "type": "commit"}})
        if args[0] == "api" and "/releases/tags/" in args[1]:
            if release is None:
                raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 404: Not Found")
            return json.dumps(release)
        if args[:3] == ("api", "--paginate", "--slurp"):
            return json.dumps([[draft_release]] if draft_release is not None else [[]])
        if "--notes-file" in args:
            notes.append(Path(args[args.index("--notes-file") + 1]).read_text())
        if args[:2] == ("release", "upload") and fail_upload:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="Upload failed")
        return ""

    monkeypatch.setattr(publisher, "gh", gh)
    return calls, notes


def publish(tmp_path, *, event="push", ref="refs/heads/main", version="0.1.0"):
    return publisher.publish(
        REPO,
        event,
        ref,
        SHA,
        version,
        assets(tmp_path, version=version, numbered=ref.startswith("refs/tags/v")),
        "https://github.com/example/omazone/actions/runs/123",
    )


def test_first_development_release_is_draft_until_assets_upload(tmp_path, monkeypatch):
    calls, notes = fake_github(monkeypatch, tag_exists=False)
    assert "Published" in publish(tmp_path)
    commands = [args[:2] for args in calls]
    assert (
        commands.index(("release", "create"))
        < commands.index(("release", "upload"))
        < commands.index(("release", "edit"))
    )
    create = next(args for args in calls if args[:2] == ("release", "create"))
    assert "--draft" in create
    assert SHA in create
    edit = calls[-1]
    assert "--prerelease=true" in edit and "--latest=false" in edit and "--draft=false" in edit
    assert all(SHA in text and "updated automatically" in text for text in notes)
    patch = next(args for args in calls if "PATCH" in args)
    assert "force=false" in patch
    assert any("POST" in args and "ref=refs/tags/development" in args for args in calls)


def test_existing_development_updates_without_deleting_release(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch, release={"draft": False})
    publish(tmp_path, event="workflow_dispatch")
    assert not any(args[:2] == ("release", "create") for args in calls)
    assert not any("delete" in args for args in calls)
    upload = next(args for args in calls if args[:2] == ("release", "upload"))
    assert "--clobber" in upload
    assert "development" in upload


def test_stale_main_build_does_not_modify_releases(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch, main_sha="b" * 40)
    assert "main has advanced" in publish(tmp_path)
    assert len(calls) == 1


def test_upload_failure_does_not_publish_new_draft(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch, fail_upload=True)
    with pytest.raises(subprocess.CalledProcessError):
        publish(tmp_path)
    assert not any(args[:2] == ("release", "edit") for args in calls)


def test_numbered_release_is_separate_and_reruns_preserve_assets(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch)
    publish(tmp_path, ref="refs/tags/v0.1.0")
    assert not any("PATCH" in args for args in calls)
    assert "--prerelease=false" in calls[-1] and "--latest=true" in calls[-1]
    calls, _ = fake_github(monkeypatch, release={"draft": False, "assets": ["existing.zip"]})
    assert "already exists" in publish(tmp_path, ref="refs/tags/v0.1.0")
    assert not any(args[:2] in (("release", "upload"), ("release", "edit")) for args in calls)


@pytest.mark.parametrize("version", ["0.4.0a1", "0.4.0b1", "1.0.0rc1", "0.4.0.dev1"])
def test_numbered_prereleases_never_replace_latest_stable(tmp_path, monkeypatch, version):
    calls, notes = fake_github(monkeypatch)
    publish(tmp_path, ref=f"refs/tags/v{version}", version=version)
    assert "--prerelease=true" in calls[-1] and "--latest=false" in calls[-1]
    assert not any("PATCH" in args for args in calls)
    assert all("not a stable release" in note for note in notes)
    calls, _ = fake_github(monkeypatch, release={"draft": False, "assets": ["existing.zip"]})
    assert "preserving" in publish(tmp_path, ref=f"refs/tags/v{version}", version=version)
    assert not any(args[:2] in (("release", "upload"), ("release", "edit")) for args in calls)


def test_versioned_notes_are_included_without_losing_generated_verification(tmp_path, monkeypatch):
    calls, notes = fake_github(monkeypatch)
    monkeypatch.setattr(publisher, "version_notes", lambda tag: "Community beta introduction")
    publish(tmp_path, ref="refs/tags/v0.4.0b1", version="0.4.0b1")
    assert notes and all("Community beta introduction" in note and SHA in note for note in notes)
    assert "--latest=false" in calls[-1]


def test_draft_retry_preserves_custom_notes_without_duplicating_build_footer(tmp_path, monkeypatch):
    body = "My testing instructions\n\n## Build and download verification\n\nOld generated footer"
    calls, notes = fake_github(
        monkeypatch,
        draft_release={
            "tag_name": "v0.4.0b1",
            "draft": True,
            "assets": [],
            "body": body,
        },
    )
    publish(tmp_path, ref="refs/tags/v0.4.0b1", version="0.4.0b1")
    assert all(note.count("## Build and download verification") == 1 for note in notes)
    assert all(
        "My testing instructions" in note and "Old generated footer" not in note for note in notes
    )
    assert "--prerelease=true" in calls[-1]


def test_empty_published_numbered_release_gets_assets(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch, release={"draft": False, "assets": []})
    assert "Published" in publish(tmp_path, ref="refs/tags/v0.1.0")
    assert not any(args[:2] == ("release", "create") for args in calls)
    upload = next(args for args in calls if args[:2] == ("release", "upload"))
    assert "--clobber" not in upload
    assert "--latest=true" in calls[-1]


def test_unpublished_numbered_draft_is_reused_and_its_notes_preserved(tmp_path, monkeypatch):
    draft = {"tag_name": "v0.1.0", "draft": True, "assets": [], "body": "User's release notes"}
    calls, notes = fake_github(monkeypatch, draft_release=draft)
    assert "Published" in publish(tmp_path, ref="refs/tags/v0.1.0")
    assert not any(args[:2] == ("release", "create") for args in calls)
    upload = next(args for args in calls if args[:2] == ("release", "upload"))
    assert "--clobber" in upload
    assert "--draft=false" in calls[-1]
    assert notes and all("User's release notes" in note and SHA in note for note in notes)


def test_duplicate_drafts_fail_without_publishing(monkeypatch):
    def github(*args):
        if args[:3] == ("api", "--paginate", "--slurp"):
            return json.dumps([[{"tag_name": "v0.1.0", "draft": True}] * 2])
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 404: Not Found")

    monkeypatch.setattr(publisher, "gh", github)
    with pytest.raises(ValueError, match="Multiple releases"):
        publisher.read_release(REPO, "v0.1.0")


def test_tag_version_mismatch_and_missing_assets_fail_before_mutation(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch)
    with pytest.raises(ValueError, match="Version tag"):
        publish(tmp_path, ref="refs/tags/v0.2.0")
    with pytest.raises(ValueError, match="assets"):
        publisher.publish(
            REPO, "push", "refs/heads/main", SHA, "0.1.0", [tmp_path / "missing.zip"], "url"
        )
    assert not calls


def test_partial_platform_assets_are_never_published(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch)
    partial = assets(tmp_path)[:-1]
    with pytest.raises(ValueError, match="release assets"):
        publisher.publish(REPO, "push", "refs/heads/main", SHA, "0.1.0", partial, "url")
    assert not calls


def test_feature_and_pr_builds_do_not_query_or_publish(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch)
    assert "artifacts only" in publish(tmp_path, event="pull_request", ref="refs/pull/1/merge")
    assert "artifacts only" in publish(
        tmp_path, event="workflow_dispatch", ref="refs/heads/feature/test"
    )
    assert not calls


def test_api_authentication_failure_is_not_treated_as_absent_release(monkeypatch):
    def fail(*args):
        raise subprocess.CalledProcessError(
            1, ["gh", *args], stderr="HTTP 401: Requires authentication"
        )

    monkeypatch.setattr(publisher, "gh", fail)
    with pytest.raises(subprocess.CalledProcessError):
        publisher.read_release(REPO, "development")
