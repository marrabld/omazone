"""Collect verified platform artifacts for one atomic release publication."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def filenames(version, numbered):
    prefix = f"Omazone-{version}-" if numbered else "Omazone-"
    return (
        ("Omazone-windows-x64", prefix + "windows-x64.zip", "BUILD-INFO.json"),
        ("Omazone-macos-arm64", prefix + "macos-arm64.zip", "BUILD-INFO-macos-arm64.json"),
        ("Omazone-arch-x86_64", prefix + "arch-x86_64.pkg.tar.zst", "BUILD-INFO-arch-x86_64.json"),
    )


def assemble(input_dir, output_dir, version, sha, numbered):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Release asset directory must start empty.")
    gathered = []
    for artifact, bundle, info_name in filenames(version, numbered):
        folder = input_dir / artifact
        archive, info_file = folder / bundle, folder / info_name
        if not archive.is_file() or archive.stat().st_size == 0 or not info_file.is_file():
            raise ValueError(f"Missing or empty {artifact} archive/build identity.")
        info = json.loads(info_file.read_text(encoding="utf-8-sig"))
        if info.get("version") != version or info.get("commit") != sha:
            raise ValueError(f"{artifact} does not match release version and commit.")
        gathered.extend((archive, info_file))
    output_dir.mkdir(parents=True, exist_ok=True)
    for file in gathered:
        shutil.copy2(file, output_dir / file.name)
    lines = []
    for archive in gathered[::2]:
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        lines.append(f"{digest.hexdigest()}  {archive.name}")
    (output_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    return [output_dir / file.name for file in gathered] + [output_dir / "SHA256SUMS.txt"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--numbered", action="store_true")
    args = parser.parse_args()
    for asset in assemble(args.input, args.output, args.version, args.sha, args.numbered):
        print(asset)


if __name__ == "__main__":
    main()
