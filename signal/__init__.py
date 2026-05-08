"""Project signal package with compatibility for Python's stdlib signal module.

The project structure intentionally contains a ``signal`` package. Because that
name collides with Python's standard library, we re-export stdlib attributes so
third-party libraries that run ``import signal`` still find the expected names.
"""

from __future__ import annotations

import importlib.util
import sys
import sysconfig
from pathlib import Path

_stdlib_signal_path = Path(sysconfig.get_path("stdlib")) / "signal.py"
_spec = importlib.util.spec_from_file_location("_stdlib_signal", _stdlib_signal_path)
if _spec and _spec.loader:
    _stdlib_signal = importlib.util.module_from_spec(_spec)
    sys.modules["_stdlib_signal"] = _stdlib_signal
    _spec.loader.exec_module(_stdlib_signal)
    for _name in dir(_stdlib_signal):
        if not _name.startswith("__"):
            globals()[_name] = getattr(_stdlib_signal, _name)

__all__ = [name for name in globals() if not name.startswith("_")]
