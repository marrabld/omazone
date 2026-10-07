#!/bin/bash
set -euo pipefail

pacman -Syu --noconfirm --needed base-devel python portaudio libsndfile libxkbcommon libglvnd fontconfig libxcb
useradd -m -u "$HOST_UID" builder
runuser -u builder -- env HOME=/home/builder UV_CACHE_DIR=/tmp/omazone-uv-cache bash -e -c '
    cd /work
    uv sync --locked --python 3.13
    uv run pyinstaller --noconfirm --distpath dist omazone.spec
    QT_QPA_PLATFORM=offscreen ./dist/Omazone/Omazone --selftest
    cd packaging/arch
    makepkg --nodeps --noconfirm --clean
'
