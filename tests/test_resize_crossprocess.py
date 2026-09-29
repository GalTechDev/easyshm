"""
Test: resize seen from another process (POSIX user-prefixed names)
===================================================================
A resize performed by one process must be visible to a process that
joined the segment before the resize, and the data segment files must
all carry the user prefix so destroy() can clean them up.
"""

import sys
import os
import glob
import time
import multiprocessing

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easyshm import EasySHM

NAME = "test_resize_xproc"
PAYLOAD = b"X" * 10_000  # larger than the initial 256 bytes -> auto-grow


def _reader(ready, written, result_queue):
    shm = EasySHM(NAME)
    try:
        ready.set()
        written.wait(5)
        # Give the listener thread time to remap the new data segment
        deadline = time.time() + 3
        data = b""
        while time.time() < deadline:
            data = shm.read()
            if data == PAYLOAD:
                break
            time.sleep(0.05)
        result_queue.put((shm.capacity, data == PAYLOAD, len(data)))
    finally:
        shm.close()


def _shm_files():
    if not os.path.isdir("/dev/shm"):
        return []
    # The flock mutex file (easyshm_mtx_*) is intentionally left in place
    return [f for f in glob.glob(f"/dev/shm/easyshm_*{NAME}*") if "_mtx_" not in f]


def test_resize_visible_from_other_process():
    writer = EasySHM(NAME, size=256)
    try:
        ready = multiprocessing.Event()
        written = multiprocessing.Event()
        results = multiprocessing.Queue()
        proc = multiprocessing.Process(target=_reader, args=(ready, written, results))
        proc.start()
        assert ready.wait(5), "Reader process did not start"

        writer.write(PAYLOAD, truncate=True)
        written.set()

        capacity, ok, length = results.get(timeout=10)
        proc.join(5)
        assert capacity >= len(PAYLOAD), f"Reader sees capacity {capacity}, expected >= {len(PAYLOAD)}"
        assert ok, f"Reader got {length} bytes instead of the {len(PAYLOAD)}-byte payload"

        if sys.platform != "win32" and hasattr(os, "getuid"):
            prefix = f"easyshm_u{os.getuid()}_"
            unprefixed = [f for f in _shm_files() if not os.path.basename(f).startswith(prefix)]
            assert not unprefixed, f"Data segments created without user prefix: {unprefixed}"
        print("[PASS] test_resize_visible_from_other_process")
    finally:
        writer.destroy()

    leftovers = _shm_files()
    assert not leftovers, f"destroy() left files behind: {leftovers}"
    print("[PASS] test_destroy_cleans_resized_segments")


if __name__ == "__main__":
    print(f"Running cross-process resize tests on {sys.platform}...\n")
    test_resize_visible_from_other_process()
    print(f"\n{'='*40}\nAll tests passed!")
