"""The version bump and lockfile must agree before any platform build."""

import runpy
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.version import Version


def test_project_version_matches_editable_package_in_lockfile():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    editable = next(package for package in packages if package["name"] == project["name"])
    assert editable["version"] == project["version"], (
        "Run uv lock after changing the project version."
    )
    assert str(Version(project["version"])) == project["version"], (
        "Use the canonical version in the exact release tag."
    )


@pytest.mark.parametrize(
    "version,short,build",
    [
        ("0.3.0", "0.3.0", "0.3.0"),
        ("0.4.0a1", "0.4.0", "0.4.0a1"),
        ("0.4.0b1", "0.4.0", "0.4.0b1"),
        ("1.0.0rc1", "1.0.0", "1.0.0fc1"),
    ],
)
def test_mac_bundle_uses_numeric_short_version_and_apple_prerelease_build(
    tmp_path, monkeypatch, version, short, build
):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    captured = {}

    def bundle(*args, **kwargs):
        captured.update(kwargs)

    runpy.run_path(
        str(root / "omazone.spec"),
        init_globals={
            "Analysis": lambda *args, **kwargs: SimpleNamespace(
                pure=[], scripts=[], binaries=[], datas=[]
            ),
            "PYZ": lambda *args, **kwargs: None,
            "EXE": lambda *args, **kwargs: None,
            "COLLECT": lambda *args, **kwargs: None,
            "BUNDLE": bundle,
        },
    )
    assert captured["version"] == short
    assert captured["info_plist"]["CFBundleVersion"] == build
    assert captured["info_plist"]["CFBundleGetInfoString"] == f"Omazone {version}"
