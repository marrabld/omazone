"""The version bump and lockfile must agree before any platform build."""

import tomllib
from pathlib import Path


def test_project_version_matches_editable_package_in_lockfile():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    editable = next(package for package in packages if package["name"] == project["name"])
    assert editable["version"] == project["version"], "Run uv lock after changing the project version."
