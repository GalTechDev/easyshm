"""
EasySHM - Windows Signal Backend
=================================
Uses kernel32.dll Named Events for zero-latency inter-process signaling.
No admin rights required (session-scoped with Local\\ prefix).
"""

import ctypes
from ctypes import wintypes
from .base import Signal

# --- kernel32 function signatures ---

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateEventW.restype = wintypes.HANDLE

_k32.PulseEvent.argtypes = [wintypes.HANDLE]
_k32.PulseEvent.restype = wintypes.BOOL

_k32.ResetEvent.argtypes = [wintypes.HANDLE]
_k32.ResetEvent.restype = wintypes.BOOL

_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD

_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL

_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_INFINITE = 0xFFFFFFFF


class Win32Signal(Signal):
    """Inter-process signal using a Windows Named Event (session-scoped).

    Uses PulseEvent behavior: waking all waiting threads/processes
    simultaneously. For processes not currently waiting, the 50ms 
    timeout in the listener thread acts as a safety fallback.
    """

    def __init__(self, name: str):
        super().__init__(name)
        # We use a manual-reset event (bManualReset=True) with PulseEvent
        # for broadcast notification.
        self._handle = _k32.CreateEventW(None, True, False, f"Local\\easyshm_{name}")
        if not self._handle:
            raise OSError(
                f"[EasySHM] CreateEventW failed for '{name}': "
                f"error {ctypes.get_last_error()}"
            )

    def emit(self):
        """Broadcast the event to all current waiters."""
        if self._handle:
            _k32.PulseEvent(self._handle)


    def wait(self, timeout_ms: int = None) -> bool:
        """Block until the event is signaled or timeout expires.

        Returns True if signaled, False on timeout.
        """
        if not self._handle:
            return False
        ms = _INFINITE if timeout_ms is None else int(timeout_ms)
        result = _k32.WaitForSingleObject(self._handle, ms)
        return result == _WAIT_OBJECT_0

    def close(self):
        """Close the handle (does not destroy the named event)."""
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def destroy(self):
        """Close the handle. Windows auto-destroys the event when
        all handles are closed system-wide."""
        self.close()
