"""
EasySHM — High-Performance Cross-Platform Shared Memory
=========================================================
A Python library for zero-socket, zero-admin inter-process communication
using OS-level shared memory and kernel signals.

Usage:
    # Process A (writer)
    shm = EasySHM("my_buffer", size=4096)
    shm.write(b"Hello from Process A!")

    # Process B (reader)
    shm = EasySHM("my_buffer")
    shm.on_update(lambda: print("Got:", shm.read()))

Supports:
    - Dynamic resizing (auto_grow)
    - Zero-copy NumPy integration (as_ndarray)
    - Kernel-level signaling (no sockets, no polling)
    - Windows (kernel32 Events) and Linux (POSIX Semaphores)
"""

import struct
import threading
import time
from typing import Any

from .segment import Segment
from .platform import Signal
from .views import ViewRegistry

__all__ = ["EasySHM"]
__version__ = "0.1.0"


# --- Control Segment Header Layout (64 bytes) ---
# All fields are little-endian.
#
#   Offset  Size  Type     Field
#   ------  ----  ------   -----
#   0       4     bytes    Magic ("ESHM")
#   4       4     uint32   seg_version   — which data segment is active (v0, v1, …)
#   8       8     uint64   data_size     — actual bytes of user data written
#   16      8     uint64   capacity      — allocated capacity of the active data segment
#   24      4     uint32   write_seq     — monotonic counter, incremented on every write
#   28      4     uint32   flags         — reserved
#   32      32    bytes    reserved
#   TOTAL = 64 bytes

_CTRL_SIZE = 256  # Over-allocate for future extensions
_HEADER_FORMAT = "<4sIQQII"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)  # 32 bytes
_MAGIC = b"ESHM"


def _pack_header(seg_version, data_size, capacity, write_seq, flags=0):
    return struct.pack(_HEADER_FORMAT, _MAGIC, seg_version, data_size, capacity, write_seq, flags)


def _unpack_header(raw):
    magic, seg_version, data_size, capacity, write_seq, flags = struct.unpack(_HEADER_FORMAT, raw[:_HEADER_SIZE])
    return {
        "magic": magic,
        "seg_version": seg_version,
        "data_size": data_size,
        "capacity": capacity,
        "write_seq": write_seq,
        "flags": flags,
    }


class EasySHM:
    """High-level shared memory object with dynamic sizing and kernel signaling.

    Args:
        name:       Unique identifier. All processes using the same name share
                    the same memory.
        size:       Initial capacity in bytes (default 4096).
                    Ignored if the segment already exists.
        auto_grow:  If True (default), write() automatically expands the
                    buffer when data exceeds the current capacity.
    """

    def __init__(self, name: str, size: int = 4096, auto_grow: bool = True):
        self.name = name
        self.auto_grow = auto_grow
        self._lock = threading.Lock()
        self._active = True

        # Callbacks
        self._on_update_callbacks = []
        self._on_resize_callbacks = []

        # Open the control segment (fixed 256 bytes, never resized)
        ctrl_name = f"easyshm_{name}_ctrl"
        self._ctrl = Segment(ctrl_name, _CTRL_SIZE)

        # Check magic to determine if we are CREATING or JOINING
        raw_magic = self._ctrl.read(0, 4)
        if raw_magic == _MAGIC:
            # --- JOINING an existing segment ---
            header = self._read_header()
            self._seg_version = header["seg_version"]
            self._data = Segment(
                f"easyshm_{name}_d{self._seg_version}",
                header["capacity"],
            )
        else:
            # --- CREATING a new segment ---
            self._seg_version = 0
            capacity = max(size, 64)  # Minimum 64 bytes
            self._data = Segment(f"easyshm_{name}_d0", capacity)
            self._write_header(0, 0, capacity, 0)

        # Kernel signal (per-segment, not per-process)
        self._signal = Signal(f"{name}_sig")

        # Listener thread for incoming updates
        self._last_write_seq = self._read_header()["write_seq"]
        self._last_seg_version = self._seg_version
        self._listener = threading.Thread(target=self._listener_loop, daemon=True)
        self._listener.start()

    # ------------------------------------------------------------------
    # Public API: Data Operations
    # ------------------------------------------------------------------

    def write(self, data: bytes | bytearray | memoryview, offset: int = 0):
        """Write data to the shared buffer.

        If auto_grow is enabled and the data exceeds the current capacity,
        the buffer is transparently expanded (segment rotation).

        Args:
            data:   Raw bytes to write.
            offset: Byte offset within the buffer (default 0).
        """
        data = bytes(data)
        required = offset + len(data)

        with self._lock:
            header = self._read_header()

            # Auto-grow if needed
            if required > header["capacity"]:
                if not self.auto_grow:
                    raise OverflowError(
                        f"[EasySHM] Data ({required} bytes) exceeds capacity "
                        f"({header['capacity']} bytes) and auto_grow is disabled."
                    )
                self._do_resize(max(required, header["capacity"] * 2))
                header = self._read_header()

            # Write the data
            self._data.write(data, offset)

            # Update header
            new_data_size = max(header["data_size"], required)
            self._write_header(
                self._seg_version,
                new_data_size,
                header["capacity"],
                header["write_seq"] + 1,
            )

        # Signal other processes
        self._signal.emit()

    def read(self, size: int = None, offset: int = 0) -> bytes:
        """Read data from the shared buffer.

        Args:
            size:   Number of bytes to read. None = read all used data.
            offset: Start position (default 0).

        Returns:
            bytes object with the data.
        """
        with self._lock:
            header = self._read_header()
            if size is None:
                size = max(0, header["data_size"] - offset)
            return self._data.read(offset, size)

    def read_view(self, offset: int = 0, size: int = None) -> memoryview:
        """Zero-copy view into the shared buffer.

        The returned memoryview points directly into the mmap.
        Changes made by other processes will be visible immediately.

        Args:
            offset: Start position.
            size:   Number of bytes. None = all used data.
        """
        with self._lock:
            header = self._read_header()
            if size is None:
                size = max(0, header["data_size"] - offset)
            return memoryview(self._data.buf)[offset:offset + size]

    def resize(self, new_capacity: int):
        """Manually expand the shared buffer.

        This triggers a segment rotation: a new, larger segment is created,
        data is copied over, and all other processes are notified.

        Args:
            new_capacity: New total capacity in bytes.
        """
        with self._lock:
            header = self._read_header()
            if new_capacity <= header["capacity"]:
                return  # No-op if already large enough
            self._do_resize(new_capacity)
        self._signal.emit()

    def as_view(self, name: str, **kwargs) -> Any:
        """Map a typed view over the shared memory buffer (zero-copy).
        
        Args:
            name:   Name of the registered view (e.g., 'numpy', 'struct', 'torch').
            kwargs: Parameters for the view (e.g., shape, dtype, type).
            
        Returns:
            A view object backed by the shared buffer.
        """
        view = ViewRegistry.get(name)
        
        # Ensure we have enough capacity if possible
        # We need a rough estimate of size required by the view
        # This is optional but helpful
        
        with self._lock:
            # The view registry will map directly into self._data.buf
            return view.map_buffer(memoryview(self._data.buf), **kwargs)

    def as_ndarray(self, shape, dtype="uint8"):
        """Return a NumPy array whose memory IS the shared buffer (zero-copy).
        
        DEPRECATED: Use as_view("numpy", shape=shape, dtype=dtype) instead.
        """
        return self.as_view("numpy", shape=shape, dtype=dtype)


    # ------------------------------------------------------------------
    # Public API: Events
    # ------------------------------------------------------------------

    def on_update(self, callback: callable):
        """Register a callback for when data changes.

        The callback is called (from the listener thread) whenever another
        process writes to this segment.

        Args:
            callback: A function with no arguments.
        """
        self._on_update_callbacks.append(callback)

    def on_resize(self, callback: callable):
        """Register a callback for when the segment is resized.

        Args:
            callback: A function receiving (new_capacity: int).
        """
        self._on_resize_callbacks.append(callback)

    def wait_update(self, timeout: float = None) -> bool:
        """Block until data is updated by another process.

        Args:
            timeout: Max wait time in seconds. None = wait forever.

        Returns:
            True if an update occurred, False on timeout.
        """
        timeout_ms = None if timeout is None else int(timeout * 1000)
        start_seq = self._read_header()["write_seq"]

        deadline = None if timeout is None else time.time() + timeout
        while self._active:
            remaining_ms = timeout_ms
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                remaining_ms = int(remaining * 1000)

            self._signal.wait(timeout_ms=min(remaining_ms or 100, 100))
            if self._read_header()["write_seq"] != start_seq:
                return True
        return False

    # ------------------------------------------------------------------
    # Public API: Metadata
    # ------------------------------------------------------------------

    @property
    def data_size(self) -> int:
        """Current number of bytes of actual data written."""
        return self._read_header()["data_size"]

    @property
    def capacity(self) -> int:
        """Current allocated capacity in bytes."""
        return self._read_header()["capacity"]

    @property
    def write_seq(self) -> int:
        """Monotonic write sequence number."""
        return self._read_header()["write_seq"]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self):
        """Release resources. The segment persists for other processes."""
        self._active = False
        if self._signal:
            self._signal.emit()  # Wake up listener so it can exit
        if self._listener and self._listener.is_alive():
            self._listener.join(timeout=1.0)
        if self._signal:
            self._signal.close()
            self._signal = None
        if self._data:
            self._data.close()
            self._data = None
        if self._ctrl:
            self._ctrl.close()
            self._ctrl = None

    def destroy(self):
        """Release resources AND delete all OS objects (files, events).

        Call this only when you are sure no other process needs this segment.
        """
        self._active = False
        if self._signal:
            self._signal.emit()
        if self._listener and self._listener.is_alive():
            self._listener.join(timeout=1.0)
        if self._signal:
            self._signal.destroy()
            self._signal = None

        # Destroy all versioned data segments
        for v in range(self._seg_version + 1):
            try:
                s = Segment(f"easyshm_{self.name}_d{v}", 1)
                s.destroy()
            except Exception:
                pass

        if self._data:
            self._data.destroy()
            self._data = None
        if self._ctrl:
            self._ctrl.destroy()
            self._ctrl = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal: Header Management
    # ------------------------------------------------------------------

    def _read_header(self) -> dict:
        raw = self._ctrl.read(0, _HEADER_SIZE)
        return _unpack_header(raw)

    def _write_header(self, seg_version, data_size, capacity, write_seq, flags=0):
        raw = _pack_header(seg_version, data_size, capacity, write_seq, flags)
        self._ctrl.write(raw, 0)

    # ------------------------------------------------------------------
    # Internal: Resize (Segment Rotation)
    # ------------------------------------------------------------------

    def _do_resize(self, new_capacity: int):
        """Create a new, larger data segment, copy data, and swap.

        This is called internally by write() (auto_grow) or resize() (manual).
        Must be called while holding self._lock.
        """
        header = self._read_header()
        old_data_size = header["data_size"]

        # Create the new data segment
        new_version = self._seg_version + 1
        new_seg = Segment(f"easyshm_{self.name}_d{new_version}", new_capacity)

        # Copy existing data from old to new
        if old_data_size > 0:
            old_data = self._data.read(0, old_data_size)
            new_seg.write(old_data, 0)

        # Swap: update control header to point to the new segment
        old_seg = self._data
        self._data = new_seg
        self._seg_version = new_version

        self._write_header(
            new_version, old_data_size, new_capacity, header["write_seq"] + 1
        )

        # Close the old segment (but don't delete — other processes may still
        # be reading from it; they'll switch when they see the version bump)
        old_seg.close()

        # Notify resize callbacks
        for cb in self._on_resize_callbacks:
            try:
                cb(new_capacity)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal: Listener Thread
    # ------------------------------------------------------------------

    def _listener_loop(self):
        """Background thread that watches for updates from other processes.

        Uses kernel signals (Events/Semaphores) for instant wake-up,
        with a fallback timeout to catch any missed signals.
        """
        while self._active:
            # Wait for a signal or timeout after 100ms
            self._signal.wait(timeout_ms=100)

            if not self._active:
                break

            header = self._read_header()

            # Check if the data segment was rotated (resize by another process)
            if header["seg_version"] != self._last_seg_version:
                with self._lock:
                    # Re-open the new data segment
                    old = self._data
                    self._data = Segment(
                        f"easyshm_{self.name}_d{header['seg_version']}",
                        header["capacity"],
                    )
                    self._seg_version = header["seg_version"]
                    self._last_seg_version = header["seg_version"]
                    if old:
                        old.close()
                for cb in self._on_resize_callbacks:
                    try:
                        cb(header["capacity"])
                    except Exception:
                        pass

            # Check if data was written
            if header["write_seq"] != self._last_write_seq:
                self._last_write_seq = header["write_seq"]
                for cb in self._on_update_callbacks:
                    try:
                        cb()
                    except Exception:
                        pass
