# Platform builds

Every merge into `main` builds Windows, Apple Silicon macOS and Arch Linux
artifacts. They are published together, so a release never contains a partial
set of platforms.

## How publication stays consistent

`release-desktop.yml` calls three build workflows and waits for all of them:

1. Windows x64: `release-windows.yml` on `windows-latest`.
2. Apple Silicon app: `build-desktop.yml` with `platform: macos` on `macos-14`.
3. Arch package: `build-desktop.yml` with `platform: arch`, inside a clean
   `archlinux` container on an Ubuntu runner.

Each platform uploads its archive and a `BUILD-INFO` file as an artifact. The
publisher downloads all artifacts, then `assemble_release_assets.py`:

- requires all three archives and all three build-identity files to be present
  and non-empty;
- requires each platform's reported version and commit to match the release being
  published, so a stale or mismatched build cannot be published;
- writes one shared `SHA256SUMS.txt` covering all three archives.

Only then does `publish_windows_release.py` create or update the release. If any
platform build fails, or its bundle fails the self-test, nothing is published and
the previous downloads stay live.

The rolling `development` release advances only by a non-forced fast-forward.
Numbered releases are immutable: a rerun never replaces their assets.

## Windows

A portable zip, not an installer. Extract it and run `Omazone.exe` with
`_internal` kept beside the executable. The build smoke-tests the executable with
`--selftest` before packaging.

The executable is unsigned. Windows SmartScreen warns on first run; choose
"More info" then "Run anyway", or verify the published checksum first.

## Apple Silicon macOS

The zip contains `Omazone.app`, built on `macos-14` and therefore targeting macOS
14 or newer on Apple Silicon. Move the app to Applications. Intel Macs are not
supported by this build.

The app is **not code-signed or notarized**, because that requires an Apple
Developer membership. The first launch therefore needs an explicit approval:

1. Open Finder, Control-click `Omazone.app`, choose **Open**.
2. Confirm **Open** in the dialog.
3. Later launches start normally.

A quarantined unsigned app is a normal result, not evidence of a broken build.
What the Mac build does verify: the bundle is produced, and
`Omazone.app/Contents/MacOS/Omazone --selftest` passes on a real macOS runner.
Audio playback on a colleague's Mac still needs their own check; see issue #14.

## Arch Linux and Omarchy

`packaging/arch` provides `PKGBUILD` plus a launcher, desktop entry and icon. The
build runs inside a clean `archlinux` container so the package never inherits
local libraries.

Install the published package directly:

```bash
sudo pacman -U Omazone-arch-x86_64.pkg.tar.zst
```

Or build it locally with `makepkg` after a normal `uv run pyinstaller` build.

The package installs the bundled application to `/opt/omazone`, an `omazone`
command in `/usr/bin`, and a desktop-menu entry. Dependencies are libraries only:
`portaudio`, `libsndfile`, `libxkbcommon`, `libglvnd`, `fontconfig` and `libxcb`.
Python and Qt are bundled, so the system Python version does not matter.

This is x86-64 only, which covers current Omarchy hardware.

### What the Arch build verifies

`verify_arch_package.sh` installs the built package into a **fresh** Arch
container, checks that the launcher, desktop entry, icon and licence are
installed, and runs `omazone --selftest` through `/usr/bin/omazone`. This proves
the package installs and launches, not that your desktop session or audio device
behaves.

## Deliberate limits

- No code signing on any platform.
- No notarization or Gatekeeper exemptions packaged into the app.
- No Apple Silicon **audio device** validation beyond the bundled self-test.
- No Intel macOS or ARM Linux builds yet.
- Packages are not published to system repositories; there is no AUR or Homebrew
  formula. An AUR recipe is a reasonable follow-up once the binary packages are
  confirmed on real machines.