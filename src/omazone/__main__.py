"""Standalone entry point for packaged builds.

PyInstaller analyses from here so the bundled executable and the console
script (`omazone`) share one code path. `--selftest` is handled in gui.main.
"""

from omazone.gui import main

if __name__ == "__main__":
    main()
