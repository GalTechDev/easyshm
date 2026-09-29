"""
EasySHM - POSIX Signal Backend (Linux / macOS fallback)
========================================================
Uses POSIX Named Semaphores for zero-latency inter-process signaling.
No root/admin rights required.
"""

import os
import errno
import fcntl
import platform
import threading
import tempfile
import time
import sys
import ctypes
import ctypes.util
from .base import Signal, Mutex


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
        """Signal all waiters (Best-effort broadcast via multiple posts)."""
        if self._sem:
            # POSIX semaphores only wake one waiter per post.
            # We post multiple times to try to cover common subscriber counts.
            # The 100ms fallback in the listener handles the rest.
            for _ in range(16):
                PosixSignal._lib.sem_post(self._sem)

    def wait(self, timeout_ms: int = None, expected: int = None) -> bool:
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


# --- Linux futex: true broadcast on a word of shared memory ---

_SYS_FUTEX = {"x86_64": 202, "amd64": 202, "aarch64": 98, "arm64": 98}.get(platform.machine().lower())
_FUTEX_WAIT = 0
_FUTEX_WAKE = 1
_WAKE_ALL = 0x7FFFFFFF


class FutexSignal(Signal):
    """Inter-process signal using a Linux futex on the header's write_seq word.

    Unlike a semaphore, FUTEX_WAKE wakes every waiter at once and leaves no
    pending token behind, so a write costs one syscall and never causes
    spurious wake-ups. wait(expected=seq) returns immediately if the word no
    longer holds `seq`, which rules out lost wake-ups. Nothing to clean up:
    the futex lives in the control segment itself.
    """

    _libc = None

    @staticmethod
    def available() -> bool:
        return sys.platform.startswith("linux") and _SYS_FUTEX is not None

    def __init__(self, name: str, buffer, offset: int):
        super().__init__(name)
        if FutexSignal._libc is None:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.syscall.restype = ctypes.c_long
            FutexSignal._libc = libc
        # Keeps a buffer export on the mmap: close() must run before the
        # control segment is closed.
        self._word = ctypes.c_uint32.from_buffer(buffer, offset)
        self._addr = ctypes.addressof(self._word)

    def _futex(self, op, val, timeout=None):
        return FutexSignal._libc.syscall(
            ctypes.c_long(_SYS_FUTEX), ctypes.c_void_p(self._addr), ctypes.c_int(op),
            ctypes.c_uint32(val), timeout, ctypes.c_void_p(0), ctypes.c_uint32(0),
        )

    def emit(self):
        """Wake every process waiting on the segment."""
        if self._word is not None:
            self._futex(_FUTEX_WAKE, _WAKE_ALL)

    def wait(self, timeout_ms: int = None, expected: int = None) -> bool:
        """Sleep until the word changes from `expected` (default: its current value)."""
        if self._word is None:
            return False
        if expected is None:
            expected = self._word.value
        ts = None
        if timeout_ms is not None:
            ts = _timespec(timeout_ms // 1000, (timeout_ms % 1000) * 1_000_000)
        r = self._futex(_FUTEX_WAIT, expected & 0xFFFFFFFF, ctypes.byref(ts) if ts else None)
        if r == 0:
            return True
        return ctypes.get_errno() == errno.EAGAIN  # already changed

    def close(self):
        """Release the view on the shared word."""
        self._word = None

    def destroy(self):
        self.close()


def _get_shm_dir():
    if os.path.isdir("/dev/shm"):
        return "/dev/shm"
    return tempfile.gettempdir()


class PosixMutex(Mutex):
    """File-based locking (flock) for inter-process mutual exclusion on POSIX.

    The owner writes its PID into the lock file and clears it before
    releasing. flock() is dropped silently by the kernel when a process dies,
    so finding a PID in the file right after acquiring means the previous
    owner crashed while holding the lock: acquire() then returns "abandoned",
    like a Windows abandoned mutex.
    """

    def __init__(self, name: str):
        super().__init__(name)
        shm_dir = _get_shm_dir()
        self._path = os.path.join(shm_dir, f"easyshm_mtx_{name}")
        self._fd = None
        # flock() does not exclude threads sharing the same file descriptor
        # (e.g. the listener thread and the main thread of one EasySHM), so
        # serialize them first.
        self._thread_lock = threading.Lock()
        self._open()

    def _open(self):
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, 'O_CLOEXEC'):
            flags |= os.O_CLOEXEC
        self._fd = os.open(self._path, flags, 0o666)

    def _is_stale(self) -> bool:
        """True if the lock file was unlinked (or replaced) by destroy() in another process."""
        try:
            return os.stat(self._path).st_ino != os.fstat(self._fd).st_ino
        except FileNotFoundError:
            return True

    def acquire(self, timeout_ms: int = None) -> bool | str:
        if not self._thread_lock.acquire(timeout=-1 if timeout_ms is None else timeout_ms / 1000.0):
            return False
        try:
            if self._acquire_file(timeout_ms):
                abandoned = bool(os.pread(self._fd, 32, 0).strip())
                os.ftruncate(self._fd, 0)
                os.pwrite(self._fd, str(os.getpid()).encode(), 0)
                return "abandoned" if abandoned else True
        except BaseException:
            self._thread_lock.release()
            raise
        self._thread_lock.release()
        return False

    def _acquire_file(self, timeout_ms: int = None) -> bool:
        end = None if timeout_ms is None else time.time() + (timeout_ms / 1000.0)
        while True:
            if self._fd is None:
                self._open()
            if end is None:
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            else:
                delay = 0.00005  # Back off from 50 µs to 5 ms: short waits stay short
                while True:
                    try:
                        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except (IOError, OSError, BlockingIOError):
                        if time.time() >= end:
                            return False
                        time.sleep(delay)
                        delay = min(delay * 2, 0.005)
            # The last user may have deleted the lock file while we were waiting:
            # the lock we hold is then on a dead inode, so switch to the new file.
            if not self._is_stale():
                return True
            os.close(self._fd)
            self._fd = None

    def release(self):
        if not self._thread_lock.locked():
            return  # Not held: never clear another owner's PID
        if self._fd is not None:
            try:
                os.ftruncate(self._fd, 0)  # Clean release: no owner left behind
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except (IOError, OSError):
                pass
        self._thread_lock.release()

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def destroy(self):
        """Delete the lock file. Must be called while holding the lock."""
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass
