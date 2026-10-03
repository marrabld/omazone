# Recreate the loop animation

The README animation is a five-second capture of Omazone's real Qt widgets and
sample-filling callback. Generated stereo audio and a simulated output device
make the capture deterministic without playing sound or requiring a display.
It shows a 1.0-2.5 second selection looping, with an original/processed switch
halfway through. GIFs do not carry audio.

From the project directory, with the uv environment installed and FFmpeg on PATH:

```bash
uv run python tools/render_loop_demo.py /tmp/opencode/omazone-loop-frames

ffmpeg -hide_banner -loglevel error -y \
  -framerate 12 -i /tmp/opencode/omazone-loop-frames/frame-%03d.png \
  -filter_complex "[0:v]scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=full:max_colors=128[p];[b][p]paletteuse=dither=none" \
  -gifflags +offsetting+transdiff -loop 0 docs/images/omazone-loop-demo.gif
```

The capture tool creates its output directory and writes 60 PNG frames. Change
the temporary path in both commands as needed. Capture deliberately forces Qt's
offscreen backend so desktop window rules cannot change frame dimensions.
FFmpeg overwrites the GIF with `-y`; generated frames stay outside the repository.
