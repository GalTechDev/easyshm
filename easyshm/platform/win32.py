"""
EasySHM - Windows Signal Backend
=================================
Uses kernel32.dll Named Events for zero-latency inter-process signaling.
No admin rights required (session-scoped with Local\\ prefix).
"""

import ctypes
from ctypes import wintypes
from .base import Signal, Mutex

# --- kernel32 function signatures ---

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateEventW.restype = wintypes.HANDLE

_k32.SetEvent.argtypes = [wintypes.HANDLE]
_k32.SetEvent.restype = wintypes.BOOL

_k32.ResetEvent.argtypes = [wintypes.HANDLE]
_k32.ResetEvent.restype = wintypes.BOOL

_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD

_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL

_k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE

_k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
_k32.ReleaseMutex.restype = wintypes.BOOL

_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
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
        """Broadcast the event by setting it briefly."""
        if self._handle:
            # SetEvent woke all current waiters on a manual-reset event.
            _k32.SetEvent(self._handle)
            _k32.ResetEvent(self._handle)


    def wait(self, timeout_ms: int = None, expected: int = None) -> bool:
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


class Win32Mutex(Mutex):
    """Named Mutex for inter-process mutual exclusion on Windows."""

    def __init__(self, name: str):
        super().__init__(name)
        # bInitialOwner = False
        self._handle = _k32.CreateMutexW(None, False, f"Local\\easyshm_mtx_{name}")
        if not self._handle:
            raise OSError(
                f"[EasySHM] CreateMutexW failed for '{name}': "
                f"error {ctypes.get_last_error()}"
            )

    def acquire(self, timeout_ms: int = None) -> bool | str:
        ms = _INFINITE if timeout_ms is None else int(timeout_ms)
        result = _k32.WaitForSingleObject(self._handle, ms)
        if result == _WAIT_OBJECT_0:
            return True
        if result == _WAIT_ABANDONED:
            return "abandoned"
        return False

    def release(self):
        if self._handle:
            _k32.ReleaseMutex(self._handle)

    def close(self):
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None
