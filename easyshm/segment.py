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

    def __init__(self, name: str, size: int):
        """Open or create a named shared memory segment.

        Args:
            name: Unique identifier for this segment.
            size: Size in bytes. If the segment already exists and is
                  larger, the existing size is used.
        """
        self.name = name
        self.size = size
        self._mmap = None
        self._fd = None
        self._filepath = None

        if sys.platform == "win32":
            self._init_windows(size)
        else:
            self._init_posix(size)

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
            fd = os.open(self._filepath, os.O_CREAT | os.O_RDWR, 0o666)
            os.ftruncate(fd, size)
            os.close(fd)

        self._fd = os.open(self._filepath, os.O_RDWR)

        # Ensure the file is large enough
        current_size = os.fstat(self._fd).st_size
        if current_size < size:
            os.ftruncate(self._fd, size)

        self._mmap = mmap.mmap(self._fd, size)

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
