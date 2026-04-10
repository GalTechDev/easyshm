"""
EasySHM - POSIX Signal Backend (Linux / macOS fallback)
========================================================
Uses POSIX Named Semaphores for zero-latency inter-process signaling.
No root/admin rights required.
"""

import ctypes
import ctypes.util
import time
import sys
from .base import Signal


# --- Locate the C library containing sem_* functions ---

def _load_sem_lib():
    """Find and load the shared library containing POSIX semaphore functions."""
    for lib_name in ("pthread", "rt", "c"):
        path = ctypes.util.find_library(lib_name)
        if path:
            try:
                lib = ctypes.CDLL(path, use_errno=True)
                # Verify sem_open exists
                _ = lib.sem_open
                return lib
            except (OSError, AttributeError):
                continue
    raise RuntimeError("[EasySHM] Could not find a library with POSIX semaphores (tried pthread, rt, c).")


# --- timespec struct for sem_timedwait ---

class _timespec(ctypes.Structure):
    _fields_ = [
        ("tv_sec", ctypes.c_long),
        ("tv_nsec", ctypes.c_long),
    ]


# --- Constants ---

_O_CREAT = 0o100
_SEM_FAILED = ctypes.c_void_p(-1).value  # (sem_t*) -1


class PosixSignal(Signal):
    """Inter-process signal using a POSIX Named Semaphore.

    Semaphore names follow the POSIX convention: /easyshm_{name}
    They appear as files in /dev/shm/ on Linux.
    """

    _lib = None  # Lazy-loaded

    def __init__(self, name: str):
        super().__init__(name)

        if PosixSignal._lib is None:
            PosixSignal._lib = _load_sem_lib()

        lib = PosixSignal._lib

        # Configure function signatures
        lib.sem_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        lib.sem_open.restype = ctypes.c_void_p

        lib.sem_post.argtypes = [ctypes.c_void_p]
        lib.sem_post.restype = ctypes.c_int

        lib.sem_wait.argtypes = [ctypes.c_void_p]
        lib.sem_wait.restype = ctypes.c_int

        lib.sem_close.argtypes = [ctypes.c_void_p]
        lib.sem_close.restype = ctypes.c_int

        lib.sem_unlink.argtypes = [ctypes.c_char_p]
        lib.sem_unlink.restype = ctypes.c_int

        # sem_timedwait may not exist on macOS
        self._has_timedwait = hasattr(lib, "sem_timedwait")
        if self._has_timedwait:
            lib.sem_timedwait.argtypes = [ctypes.c_void_p, ctypes.POINTER(_timespec)]
            lib.sem_timedwait.restype = ctypes.c_int

        lib.sem_trywait.argtypes = [ctypes.c_void_p]
        lib.sem_trywait.restype = ctypes.c_int

        # Open or create the named semaphore (initial value = 0)
        sem_name = f"/easyshm_{name}".encode("utf-8")
        self._sem_name = sem_name
        self._sem = lib.sem_open(sem_name, _O_CREAT, 0o666, 0)

        if self._sem == _SEM_FAILED:
            errno = ctypes.get_errno()
            raise OSError(
                f"[EasySHM] sem_open failed for '{name}': errno {errno}"
            )

    def emit(self):
        """Post (increment) the semaphore, waking one waiter."""
        if self._sem:
            PosixSignal._lib.sem_post(self._sem)

    def wait(self, timeout_ms: int = None) -> bool:
        """Wait on the semaphore. Returns True if signaled, False on timeout."""
        if not self._sem:
            return False

        lib = PosixSignal._lib

        if timeout_ms is None:
            # Blocking wait (no timeout)
            result = lib.sem_wait(self._sem)
            return result == 0

        if self._has_timedwait:
            # Use sem_timedwait with absolute deadline
            deadline = time.time() + (timeout_ms / 1000.0)
            ts = _timespec(
                int(deadline),
                int((deadline % 1) * 1_000_000_000),
            )
            result = lib.sem_timedwait(self._sem, ctypes.byref(ts))
            return result == 0
        else:
            # Fallback for systems without sem_timedwait (macOS):
            # Busy-poll with sem_trywait and short sleeps.
            end_time = time.time() + (timeout_ms / 1000.0)
            while time.time() < end_time:
                if lib.sem_trywait(self._sem) == 0:
                    return True
                time.sleep(0.001)  # 1ms polling interval
            return False

    def close(self):
        """Close the semaphore handle (does not destroy the named object)."""
        if self._sem:
            PosixSignal._lib.sem_close(self._sem)
            self._sem = None

    def destroy(self):
        """Close the handle AND remove the named semaphore from the system."""
        self.close()
        if self._sem_name:
            PosixSignal._lib.sem_unlink(self._sem_name)
            self._sem_name = None
