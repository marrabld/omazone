"""Omazone: readable DSP first, offline audio processing."""

import importlib.metadata as _metadata

try:
    import PySide6 as _pyside6

    if not hasattr(_pyside6, "__version__"):
        # PySide6-Essentials installs the Qt modules but not the metapackage's
        # version stub. pyqtgraph reads PySide6.__version__ at import time.
        try:
            _pyside6.__version__ = _metadata.version("pyside6-essentials")
        except _metadata.PackageNotFoundError:  # pragma: no cover - frozen builds
            _pyside6.__version__ = _pyside6.QtCore.__version__
        _pyside6.__version_info__ = tuple(
            int(part) for part in _pyside6.__version__.split(".")[:3]
        )
except ImportError:  # pragma: no cover - headless numeric-only use
    pass
