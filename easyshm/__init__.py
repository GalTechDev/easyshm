import sys
import os
import struct
import threading
import time
import zlib
import glob
from typing import Any, List, Dict

from .segment import Segment
from .platform import Signal, Mutex


class LockAbandonedError(Exception):
    """Raised when an IPC Mutex is acquired but was abandoned by a crashed process."""
    pass


class ProtocolIncompatibilityError(Exception):
    """Raised when the existing segment has an incompatible protocol version."""
    pass
from .views import ViewRegistry

__all__ = ["EasySHM", "LockAbandonedError", "ProtocolIncompatibilityError"]

# Header Layout (256 bytes)
# ========================
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
#   32      4     uint32   checksum      — CRC32 of the header
#   36      4     uint32   reserved      — padding
#   TOTAL = 40 bytes used (out of 256)

_CTRL_SIZE = 256  # Over-allocate for future extensions
_PROTOCOL_VERSION = 1
_HEADER_FORMAT = "<4sIQQIIII"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)  # 40 bytes
_MAGIC = b"ESHM"


def _calculate_checksum(raw_header):
    # Calculate CRC32 on everything except the last 4 bytes (the checksum field)
    return zlib.crc32(raw_header[:_HEADER_SIZE - 4]) & 0xFFFFFFFF


def _pack_header(seg_version, data_size, capacity, write_seq, flags=0):
    # Pack without checksum first
    raw = struct.pack(_HEADER_FORMAT, _MAGIC, seg_version, data_size, capacity, write_seq, flags, _PROTOCOL_VERSION, 0)
    # Calculate and insert checksum
    checksum = _calculate_checksum(raw)
    return struct.pack(_HEADER_FORMAT, _MAGIC, seg_version, data_size, capacity, write_seq, flags, _PROTOCOL_VERSION, checksum)


def _unpack_header(raw):
    magic, seg_version, data_size, capacity, write_seq, flags, proto_ver, checksum = struct.unpack(_HEADER_FORMAT, raw[:_HEADER_SIZE])
    
    # Verify checksum
    actual_checksum = _calculate_checksum(raw[:_HEADER_SIZE])
    if checksum != actual_checksum:
        raise ValueError(f"Header checksum mismatch: expected {checksum:08x}, got {actual_checksum:08x}")
        
    return {
        "magic": magic,
        "seg_version": seg_version,
        "data_size": data_size,
        "capacity": capacity,
        "write_seq": write_seq,
        "flags": flags,
        "protocol_version": proto_ver,
        "checksum": checksum,
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

    def __init__(
        self,
        name: str,
        size: int = 4096,
        auto_grow: bool = True,
        auto_shrink: bool = False,
        mode: int = 0o600,
        auto_recover_mutex: bool = True,
        pinned: bool = False,
        on_pin_fail: str = "error"
    ):
        self.name = name
        self.auto_grow = auto_grow
        self.auto_shrink = auto_shrink
        self.pinned = pinned
        self.on_pin_fail = on_pin_fail
        self._initial_size = size
        self.auto_recover_mutex = auto_recover_mutex
        self._lock = threading.Lock() # Thread lock
        
        # User Isolation (POSIX only)
        self._user_prefix = ""
        if sys.platform != "win32" and hasattr(os, "getuid"):
            self._user_prefix = f"u{os.getuid()}_"
            
        full_name = f"{self._user_prefix}{name}"
        self._ipc_lock = Mutex(full_name) # Process lock
        self._active = True
        self._last_gc_time = 0.0

        # Callbacks
        self._on_update_callbacks = []
        self._on_resize_callbacks = []

        # Open the control segment (fixed 256 bytes, never resized)
        ctrl_name = f"easyshm_{name}_ctrl"
        
        with self._ipc_lock as lock_res:
            if lock_res == "abandoned" and not self.auto_recover_mutex:
                raise LockAbandonedError(f"[EasySHM] Mutex for '{full_name}' was abandoned. Data may be inconsistent.")
            
            self._ctrl = Segment(f"easyshm_{full_name}_ctrl", _CTRL_SIZE, mode=mode)

            # Check magic to determine if we are CREATING or JOINING
            header = None
            raw_magic = self._ctrl.read(0, 4)
            if raw_magic == _MAGIC:
                # JOINING: Wait for full initialization if needed
                for attempt in range(20):
                    try:
                        header = self._read_header()
                        if header["capacity"] > 0:
                            break
                    except ValueError:
                         # Mid-write or checksum mismatch during init
                         pass
                    time.sleep(0.05)
            
            if header:
                # --- JOINING: Compatibility Check ---
                if header["protocol_version"] != _PROTOCOL_VERSION:
                    raise ProtocolIncompatibilityError(
                        f"[EasySHM] Incompatible protocol version: segment={header['protocol_version']}, "
                        f"library={_PROTOCOL_VERSION}. Please call destroy() first."
                    )
                
                # --- JOINING: Open existing data segment ---
                self._seg_version = header["seg_version"]
                self._data = Segment(
                    f"easyshm_{full_name}_d{self._seg_version}",
                    header["capacity"],
                    mode=mode,
                    pinned=self.pinned,
                    on_pin_fail=self.on_pin_fail
                )
            else:
                # --- CREATING: Initialize header and data segment ---
                self._seg_version = 0
                capacity = max(size, 64)  # Minimum 64 bytes
                self._data = Segment(
                    f"easyshm_{full_name}_d0", 
                    capacity, 
                    mode=mode, 
                    pinned=self.pinned, 
                    on_pin_fail=self.on_pin_fail
                )
                self._write_header(0, 0, capacity, 0)
        
        # Cleanup orphan segments from previous runs or other versions
        self._cleanup_orphans()

        # Kernel signal (per-segment, not per-process)
        self._signal = Signal(f"{full_name}_sig")

        # Listener thread for incoming updates
        self._last_write_seq = self._read_header()["write_seq"]
        self._last_seg_version = self._seg_version
        self._listener = threading.Thread(target=self._listener_loop, daemon=True)
        self._listener.start()


    # ------------------------------------------------------------------
    # Public API: Data Operations
    # ------------------------------------------------------------------

    def write(self, data: bytes | bytearray | memoryview, offset: int = 0, truncate: bool = False):
        """Write data to the shared buffer."""
        data = bytes(data)
        required = offset + len(data)

        # Custom lock acquisition to handle abandoned state
        lock_res = self._ipc_lock.acquire()
        if lock_res is False:
             raise TimeoutError(f"[EasySHM] Could not acquire IPC lock for writing")
        if lock_res == "abandoned" and not self.auto_recover_mutex:
            self._ipc_lock.release()
            raise LockAbandonedError("[EasySHM] Mutex was abandoned during write. Data may be inconsistent.")

        try:
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
                new_data_size = required if truncate else max(header["data_size"], required)
                self._write_header(
                    self._seg_version,
                    new_data_size,
                    header["capacity"],
                    header["write_seq"] + 1,
                )

                # Auto-shrink if enabled
                if self.auto_shrink and new_data_size < header["capacity"] * 0.25:
                    # Target 50% capacity, but don't go below initial size
                    target_capacity = max(self._initial_size, header["capacity"] // 2)
                    if target_capacity < header["capacity"]:
                        self._do_resize(target_capacity)
        finally:
            self._ipc_lock.release()

        # Notify other processes
        self._signal.emit()
        
        # Cleanup obsolete segment if no one is using it anymore
        self._cleanup_orphans(force=True)

    def read(self, size: int = None, offset: int = 0) -> bytes:
        """Read data from the shared buffer."""
        with self._ipc_lock as lock_res:
             if lock_res == "abandoned" and not self.auto_recover_mutex:
                 raise LockAbandonedError("[EasySHM] Mutex was abandoned. Data may be inconsistent.")
             with self._lock:
                header = self._read_header()
                if size is None:
                    size = max(0, header["data_size"] - offset)
                return self._data.read(offset, size)

    def read_view(self, offset: int = 0, size: int = None) -> memoryview:
        """Zero-copy view into the shared buffer."""
        with self._ipc_lock as lock_res:
             if lock_res == "abandoned" and not self.auto_recover_mutex:
                 raise LockAbandonedError("[EasySHM] Mutex was abandoned. Data may be inconsistent.")
             with self._lock:
                header = self._read_header()
                if size is None:
                    size = max(0, header["data_size"] - offset)
                return memoryview(self._data.buf)[offset:offset + size]

    def resize(self, new_capacity: int):
        """Manually resize the shared buffer."""
        with self._ipc_lock as lock_res:
             if lock_res == "abandoned" and not self.auto_recover_mutex:
                 raise LockAbandonedError("[EasySHM] Mutex was abandoned. Data may be inconsistent.")
             with self._lock:
                header = self._read_header()
                if new_capacity == header["capacity"]:
                    return
                self._do_resize(new_capacity)
        self._signal.emit()

    def as_view(self, name: str, **kwargs) -> Any:
        """Map a typed view over the shared memory buffer (zero-copy)."""
        view = ViewRegistry.get(name)
        
        with self._ipc_lock as lock_res:
             if lock_res == "abandoned" and not self.auto_recover_mutex:
                 raise LockAbandonedError("[EasySHM] Mutex was abandoned. Data may be inconsistent.")
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
        """Register a callback for when data changes."""
        self._on_update_callbacks.append(callback)

    def on_resize(self, callback: callable):
        """Register a callback for when the segment is resized."""
        self._on_resize_callbacks.append(callback)

    def wait_update(self, timeout: float = None) -> bool:
        """Block until data is updated by another process."""
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
            header = self._read_header()
            if header["write_seq"] != start_seq:
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
        if hasattr(self, '_signal') and self._signal:
            self._signal.emit()  # Wake up listener so it can exit
        if hasattr(self, '_listener') and self._listener and self._listener.is_alive():
            self._listener.join(timeout=1.0)
        if hasattr(self, '_signal') and self._signal:
            self._signal.close()
            self._signal = None
        if hasattr(self, '_data') and self._data:
            self._data.close()
            self._data = None
        if hasattr(self, '_ctrl') and self._ctrl:
            self._ctrl.close()
            self._ctrl = None

    def destroy(self):
        """Release resources AND delete all OS objects (files, events)."""
        self._active = False
        if hasattr(self, '_signal') and self._signal:
            self._signal.emit()
        if hasattr(self, '_listener') and self._listener and self._listener.is_alive():
            self._listener.join(timeout=1.0)
        if hasattr(self, '_signal') and self._signal:
            self._signal.destroy()
            self._signal = None

        # Destroy all versioned data segments
        for v in range(getattr(self, '_seg_version', 0) + 1):
            try:
                full_name = f"{self._user_prefix}{self.name}"
                s = Segment(f"easyshm_{full_name}_d{v}", 1)
                s.destroy()
            except Exception:
                pass

        if hasattr(self, '_data') and self._data:
            self._data.destroy()
            self._data = None
        if hasattr(self, '_ctrl') and self._ctrl:
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
        """Read and verify the control header with retries."""
        last_err = None
        for attempt in range(5):
            try:
                raw = self._ctrl.read(0, _HEADER_SIZE)
                return _unpack_header(raw)
            except ValueError as e:
                last_err = e
                # Maybe mid-write, wait a tiny bit and retry
                time.sleep(0.005)
        
        raise ValueError(f"Failed to read a valid header after 5 attempts: {last_err}")

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
        new_seg = Segment(
            f"easyshm_{self.name}_d{new_version}", 
            new_capacity,
            mode=getattr(self._data, 'mode', 0o600),
            pinned=self.pinned,
            on_pin_fail=self.on_pin_fail
        )

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
        if old_seg:
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
        """Background thread that watches for updates from other processes."""
        while self._active:
            # Wait for a signal or timeout after 100ms
            self._signal.wait(timeout_ms=100)

            if not self._active:
                break

            try:
                header = self._read_header()
            except ValueError:
                continue

            # Check if the data segment was rotated (resize by another process)
            if header["seg_version"] != self._last_seg_version:
                try:
                    with self._ipc_lock as lock_res:
                        if lock_res == "abandoned" and not self.auto_recover_mutex:
                             break
                        with self._lock:
                            # Re-open the new data segment
                            old = self._data
                            full_name = f"{self._user_prefix}{self.name}"
                            self._data = Segment(
                                f"easyshm_{full_name}_d{header['seg_version']}",
                                header["capacity"],
                                mode=getattr(self._data, 'mode', 0o600),
                                pinned=self.pinned,
                                on_pin_fail=self.on_pin_fail
                            )
                        self._seg_version = header["seg_version"]
                        self._last_seg_version = header["seg_version"]
                        if old:
                            old.close()
                except (TimeoutError, LockAbandonedError):
                    pass
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
                
                # Try a cleanup when we detect a change (could have been a resize)
                self._cleanup_orphans(force=True)

    def _cleanup_orphans(self, force=False):
        """Scan for orphaned data segments and delete them if unused."""
        if sys.platform == "win32":
            return
            
        now = time.time()
        # Cooldown of 60 seconds unless forced (e.g. on write/resize)
        if not force and (now - self._last_gc_time) < 60:
            return
        
        self._last_gc_time = now

        try:
            import fcntl
            from .segment import _get_shm_dir
        except ImportError:
            return

        shm_dir = _get_shm_dir()
        full_name = f"{self._user_prefix}{self.name}"
        
        # Patterns for segments and semaphores
        # Data segments: easyshm_{full_name}_d*
        # Control: easyshm_{full_name}_ctrl
        # Sems: sem.easyshm_{full_name}_sig
        patterns = [
            os.path.join(shm_dir, f"easyshm_{full_name}_d*"),
            os.path.join(shm_dir, f"easyshm_{full_name}_ctrl"),
            os.path.join(shm_dir, f"sem.easyshm_{full_name}_sig"),
        ]
        
        current_files = {
            os.path.join(shm_dir, f"easyshm_{full_name}_d{self._seg_version}"),
            os.path.join(shm_dir, f"easyshm_{full_name}_ctrl"),
            os.path.join(shm_dir, f"sem.easyshm_{full_name}_sig"),
        }

        for pattern in patterns:
            for filepath in glob.glob(pattern):
                if filepath in current_files:
                    continue
                
                # Check if it exists (it might have been deleted by another process already)
                if not os.path.exists(filepath):
                    continue

                try:
                    fd = os.open(filepath, os.O_RDWR)
                    try:
                        # Success if we can get an EXCLUSIVE lock
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        os.unlink(filepath)
                    except (OSError, IOError):
                        pass
                    finally:
                        os.close(fd)
                except (OSError, IOError):
                    pass
