"""
EasySHM - Low-Level Named Shared Memory Segment
=================================================
Cross-platform mmap-based shared memory with file backing.

Windows: uses page-file-backed named mmap (tagname).
Linux:   uses file-backed mmap in /dev/shm (tmpfs / RAM).
"""

import mmap
import os
import sys
import tempfile


def _get_shm_dir():
    """Return the best directory for shared memory files."""
    if sys.platform == "win32":
        # Windows: no file needed for named mmap, return None
        return None
    # Linux: prefer /dev/shm (tmpfs = RAM-backed)
    if os.path.isdir("/dev/shm"):
        return "/dev/shm"
    # Fallback to /tmp
    return tempfile.gettempdir()


class Segment:
    """A fixed-size named shared memory segment backed by mmap.

    This is the lowest-level building block. It maps a named region
    of memory that can be accessed by multiple processes simultaneously.

    On Windows, uses page-file-backed named mmap (no disk file).
    On Linux, uses a file in /dev/shm (tmpfs, lives in RAM).
    """

    def __init__(self, name: str, size: int, mode: int = 0o600, pinned: bool = False, on_pin_fail: str = "error"):
        """Open or create a named shared memory segment.

        Args:
            name: Unique identifier for this segment.
            size: Size in bytes. If the segment already exists and is
                  larger, the existing size is used.
            mode: Octal permissions for the backing file (Linux only, default 0o600).
            pinned: If True, locks the memory in physical RAM (no swap). Required for GPU DMA.
            on_pin_fail: "error" to raise exception, "warn" to log warning and continue.
        """
        self.name = name
        self.size = size
        self.mode = mode
        self.pinned = pinned
        self._mmap = None
        self._fd = None
        self._filepath = None

        if sys.platform == "win32":
            self._init_windows(size)
        else:
            self._init_posix(size)
            
        if self.pinned:
            self.pin(on_pin_fail)

    def pin(self, on_pin_fail="error"):
        """Lock the memory in physical RAM."""
        try:
            if sys.platform == "win32":
                self._pin_windows()
            else:
                self._pin_posix()
        except Exception as e:
            if on_pin_fail == "error":
                raise RuntimeError(f"[EasySHM] Failed to pin memory for '{self.name}': {e}")
            else:
                print(f"[EasySHM] WARNING: Failed to pin memory for '{self.name}': {e}")

    def _pin_windows(self):
        import ctypes
        from ctypes import wintypes

        # 1. Increase Working Set Size to allow VirtualLock to succeed
        # VirtualLock is limited by the process's working set limits.
        kernel32 = ctypes.windll.kernel32
        GetCurrentProcess = kernel32.GetCurrentProcess
        GetCurrentProcess.restype = wintypes.HANDLE
        
        SetProcessWorkingSetSize = kernel32.SetProcessWorkingSetSize
        SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
        SetProcessWorkingSetSize.restype = wintypes.BOOL

        process = GetCurrentProcess()
        # We try to set the working set size to at least the size of our segment + some margin
        margin = 1024 * 1024 * 32 # 32MB
        new_size = self.size + margin
        
        # We don't know the current limits, so we try to set them. 
        # On modern Windows, this usually works unless restricted by policy.
        SetProcessWorkingSetSize(process, new_size, new_size)

        # 2. Perform the lock
        VirtualLock = kernel32.VirtualLock
        VirtualLock.argtypes = [wintypes.LPVOID, ctypes.c_size_t]
        VirtualLock.restype = wintypes.BOOL
        
        # Get pointer to mmap buffer
        ptr = ctypes.addressof(ctypes.c_char.from_buffer(self._mmap, 0))
        if not VirtualLock(ptr, self.size):
            raise ctypes.WinError(ctypes.get_last_error())

    def _pin_posix(self):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        
        # mlock(const void *addr, size_t len)
        mlock = libc.mlock
        mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        mlock.restype = ctypes.c_int
        
        ptr = ctypes.addressof(ctypes.c_char.from_buffer(self._mmap, 0))
        if mlock(ptr, self.size) != 0:
            errno = ctypes.get_errno()
            import os
            raise OSError(errno, os.strerror(errno))

    def _init_windows(self, size: int):
        """Windows: page-file-backed named mmap (no admin, no disk file)."""
        # tagname creates/opens a named file mapping object.
        # If it already exists, we attach to it.
        self._mmap = mmap.mmap(-1, size, tagname=self.name)

    def _init_posix(self, size: int):
        """Linux: file in /dev/shm backed by tmpfs (RAM)."""
        shm_dir = _get_shm_dir()
        self._filepath = os.path.join(shm_dir, self.name)

        # Create the backing file if it doesn't exist
        if not os.path.exists(self._filepath):
            fd = os.open(self._filepath, os.O_CREAT | os.O_RDWR, self.mode)
            os.ftruncate(fd, size)
            os.close(fd)
            # Ensure mode is applied even if file already existed with wrong mode
            os.chmod(self._filepath, self.mode)

        self._fd = os.open(self._filepath, os.O_RDWR)

        # Ensure the file is large enough
        current_size = os.fstat(self._fd).st_size
        if current_size < size:
            os.ftruncate(self._fd, size)

        self._mmap = mmap.mmap(self._fd, size)
        
        # Take a shared lock on the segment to mark it as 'in use'.
        # This allows the GC to safely identify segments with 0 users.
        try:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_SH)
        except (ImportError, OSError, IOError):
            # If we can't get a shared lock, something is weird, 
            # but we proceed as mmap itself is the primary mechanism.
            pass

    @property
    def buf(self) -> mmap.mmap:
        """Direct access to the underlying mmap buffer.

        Use this for zero-copy operations:
            segment.buf[0:10] = b'0123456789'
            view = memoryview(segment.buf)
        """
        return self._mmap

    def read(self, offset: int = 0, size: int = None) -> bytes:
        """Read bytes from the segment.

        Args:
            offset: Start position.
            size: Number of bytes. None = read from offset to end.
        """
        if size is None:
            size = self.size - offset
        self._mmap.seek(offset)
        return self._mmap.read(size)

    def write(self, data: bytes, offset: int = 0):
        """Write bytes to the segment at the given offset."""
        self._mmap.seek(offset)
        self._mmap.write(data)

    def close(self):
        """Close the mmap and file descriptor. Does NOT delete the segment."""
        if self._mmap:
            self._mmap.close()
            self._mmap = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def destroy(self):
        """Close the segment AND delete the backing file (Linux only).
        On Windows, the page-file-backed mapping is auto-cleaned by the OS.
        """
        self.close()
        if self._filepath and os.path.exists(self._filepath):
            try:
                os.unlink(self._filepath)
            except OSError:
                pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
