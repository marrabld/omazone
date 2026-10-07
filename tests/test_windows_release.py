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


def assets(tmp_path):
    files = [
        tmp_path / "Omazone-windows-x64.zip",
        tmp_path / "SHA256SUMS.txt",
        tmp_path / "BUILD-INFO.json",
    ]
    for path in files:
        path.write_bytes(b"verified-build")
    return files


def fake_github(monkeypatch, *, main_sha=SHA, release=None, tag_exists=True, fail_upload=False):
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
        assets(tmp_path),
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


def test_empty_published_numbered_release_gets_assets(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch, release={"draft": False, "assets": []})
    assert "Published" in publish(tmp_path, ref="refs/tags/v0.1.0")
    assert not any(args[:2] == ("release", "create") for args in calls)
    upload = next(args for args in calls if args[:2] == ("release", "upload"))
    assert "--clobber" not in upload
    assert "--latest=true" in calls[-1]


def test_tag_version_mismatch_and_missing_assets_fail_before_mutation(tmp_path, monkeypatch):
    calls, _ = fake_github(monkeypatch)
    with pytest.raises(ValueError, match="Version tag"):
        publish(tmp_path, ref="refs/tags/v0.2.0")
    with pytest.raises(ValueError, match="assets"):
        publisher.publish(
            REPO, "push", "refs/heads/main", SHA, "0.1.0", [tmp_path / "missing.zip"], "url"
        )
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
