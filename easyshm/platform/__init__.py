"""
EasySHM - Platform Auto-Detection
===================================
Exports the correct Signal class for the current OS.
"""

import sys

if sys.platform == "win32":
    from .win32 import Win32Signal as Signal, Win32Mutex as Mutex
else:
    from .posix import PosixSignal as Signal, PosixMutex as Mutex

__all__ = ["Signal", "Mutex"]
