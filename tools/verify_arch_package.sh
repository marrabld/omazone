#!/bin/bash
# Install the built package in a clean Arch container and run the installed launcher.
set -euo pipefail

pacman -Syu --noconfirm --needed portaudio libsndfile libxkbcommon libglvnd fontconfig libxcb
pacman -U --noconfirm /work/packaging/arch/omazone-bin-*.pkg.tar.zst
command -v omazone
for expected in /usr/bin/omazone /usr/share/applications/omazone.desktop \
  /usr/share/icons/hicolor/scalable/apps/omazone.svg /usr/share/licenses/omazone-bin/LICENSE \
  /opt/omazone/Omazone; do
  test -f "$expected" || { echo "Missing packaged file: $expected" >&2; exit 1; }
done
QT_QPA_PLATFORM=offscreen omazone --selftest