"""All platform bundles must agree on version/commit before release publication."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "assemble_release_assets.py"
spec = importlib.util.spec_from_file_location("assemble_release_assets", SCRIPT)
assembler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembler)

SHA = "b" * 40


def bundles(tmp_path, version="0.2.0", numbered=False):
    source = tmp_path / "artifacts"
    for artifact, name, info in assembler.filenames(version, numbered):
        folder = source / artifact
        folder.mkdir(parents=True)
        (folder / name).write_bytes(name.encode())
        (folder / info).write_text(json.dumps({"version": version, "commit": SHA}))
    return source


@pytest.mark.parametrize("numbered", [False, True])
def test_assemble_all_platform_assets_with_single_checksum_file(tmp_path, numbered):
    source = bundles(tmp_path, numbered=numbered)
    output = tmp_path / "release"
    gathered = assembler.assemble(source, output, "0.2.0", SHA, numbered)
    assert len(gathered) == 7 and all(path.is_file() for path in gathered)
    lines = (output / "SHA256SUMS.txt").read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        digest, name = line.split("  ")
        assert digest == hashlib.sha256((output / name).read_bytes()).hexdigest()


def test_assembly_fails_before_creating_release_dir_for_missing_or_wrong_build(tmp_path):
    source = bundles(tmp_path)
    output = tmp_path / "release"
    arch = source / "Omazone-arch-x86_64" / "BUILD-INFO-arch-x86_64.json"
    arch.write_text(json.dumps({"version": "0.1.0", "commit": SHA}))
    with pytest.raises(ValueError, match="does not match"):
        assembler.assemble(source, output, "0.2.0", SHA, False)
    assert not output.exists()
    arch.write_text(json.dumps({"version": "0.2.0", "commit": SHA}))
    archive = next((source / "Omazone-macos-arm64").glob("*.zip"))
    archive.unlink()
    with pytest.raises(ValueError, match="Missing or empty"):
        assembler.assemble(source, output, "0.2.0", SHA, False)
    assert not output.exists()


def test_platform_supplied_checksum_file_is_rejected(tmp_path):
    source = bundles(tmp_path)
    output = tmp_path / "release"
    # A per-platform checksum file could hide a stale or incomplete shared list.
    (source / "Omazone-windows-x64" / "SHA256SUMS.txt").write_text("deadbeef  other.zip\n")
    with pytest.raises(ValueError, match="unexpected files"):
        assembler.assemble(source, output, "0.2.0", SHA, False)
    assert not output.exists()
