"""
EasySHM - Platform Auto-Detection
===================================
Exports the correct Signal class for the current OS.
"""

import sys

if sys.platform == "win32":
    from .win32 import Win32Signal as Signal
else:
    from .posix import PosixSignal as Signal

__all__ = ["Signal"]
