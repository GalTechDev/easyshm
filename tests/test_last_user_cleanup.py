"""
Test: the last process to close() a segment deletes it
=======================================================
Every EasySHM instance holds a shared flock on the control segment; the
kernel releases it when the process closes the segment, exits or crashes.
close() uses this to delete every OS object only when nobody else uses it.
"""

import sys
import os
import glob
import time
import signal
import subprocess
import multiprocessing

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easyshm import EasySHM

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _files(name):
    """Every /dev/shm object belonging to this segment (data, ctrl, semaphore, mutex)."""
    full = f"u{os.getuid()}_{name}"
    return sorted(
        glob.glob(f"/dev/shm/easyshm_{full}_*")
        + glob.glob(f"/dev/shm/easyshm_mtx_{full}")
        + glob.glob(f"/dev/shm/sem.easyshm_{full}_sig")
    )


def _spawn_holder(name, payload=b""):
    """Start a process that opens the segment and keeps it open until killed."""
    code = (
        "import sys, time; sys.path.insert(0, sys.argv[1]); from easyshm import EasySHM; "
        f"s = EasySHM({name!r}); s.write({payload!r}) if {payload!r} else None; "
        "print('ready', flush=True); time.sleep(60)"
    )
    p = subprocess.Popen([sys.executable, "-c", code, ROOT], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready", "Holder process did not start"
    return p


def test_last_close_removes_everything():
    name = "test_last_user"
    a = EasySHM(name, size=256)
    b = EasySHM(name)
    a.write(b"hello")
    a.write(b"X" * 1000)  # force a resize so there are several data versions

    a.close()
    assert _files(name), "Files were removed while another user was still open"
    assert b.read() == b"X" * 1000, "Remaining user lost access to the data"
    b.write(b"still works", truncate=True)
    assert b.read() == b"still works"

    b.close()
    leftovers = _files(name)
    assert not leftovers, f"Last close left files behind: {leftovers}"
    print("[PASS] test_last_close_removes_everything")


def test_other_process_keeps_segment_alive():
    name = "test_last_user_xproc"
    holder = _spawn_holder(name, b"from child")
    try:
        shm = EasySHM(name)
        assert shm.read() == b"from child"
        shm.close()
        assert _files(name), "Segment deleted while another process still uses it"

        shm = EasySHM(name)
        assert shm.read() == b"from child", "Data lost after a non-last close"
    finally:
        holder.terminate()
        holder.wait()
    shm.close()
    leftovers = _files(name)
    assert not leftovers, f"Files left after the last user closed: {leftovers}"
    print("[PASS] test_other_process_keeps_segment_alive")


def test_crashed_process_is_not_counted():
    name = "test_last_user_crash"
    holder = _spawn_holder(name)
    shm = EasySHM(name)
    holder.send_signal(signal.SIGKILL)  # no close(), the kernel drops its flocks
    holder.wait()

    shm.close()
    leftovers = _files(name)
    assert not leftovers, f"A crashed process kept the segment alive: {leftovers}"
    print("[PASS] test_crashed_process_is_not_counted")


def test_persistent_survives_close():
    name = "test_last_user_persist"
    shm = EasySHM(name, persistent=True)
    shm.write(b"keep me")
    shm.close()
    assert _files(name), "persistent=True segment was deleted on close()"

    shm = EasySHM(name, persistent=True)
    assert shm.read() == b"keep me", "persistent data was lost"
    shm.destroy()
    leftovers = _files(name)
    assert not leftovers, f"destroy() left files behind: {leftovers}"
    print("[PASS] test_persistent_survives_close")


def test_reopen_after_cleanup():
    name = "test_last_user_reopen"
    shm = EasySHM(name)
    shm.write(b"first life")
    shm.close()
    shm.close()  # idempotent: must not recreate the mutex file
    assert not _files(name)

    shm = EasySHM(name)
    assert shm.read() == b"", "A new segment should start empty"
    shm.write(b"second life")
    assert shm.read() == b"second life"
    shm.close()
    assert not _files(name)
    print("[PASS] test_reopen_after_cleanup")


def test_prefix_name_not_touched():
    other = EasySHM("test_pfx_dog", persistent=True)
    other.write(b"not yours")
    other.close()
    before = _files("test_pfx_dog")

    shm = EasySHM("test_pfx")
    shm.write(b"x" * 10_000)  # resize -> orphan cleanup runs
    shm.close()
    assert _files("test_pfx_dog") == before, "Cleanup of 'test_pfx' deleted 'test_pfx_dog' files"

    EasySHM("test_pfx_dog", persistent=True).destroy()
    print("[PASS] test_prefix_name_not_touched")


def _churn(name, rounds, errors):
    try:
        for i in range(rounds):
            shm = EasySHM(name, size=64)
            shm.write(f"{os.getpid()}:{i}".encode(), truncate=True)
            shm.read()
            shm.close()
    except Exception as e:  # pragma: no cover - reported to the parent
        errors.put(repr(e))


def test_concurrent_open_close():
    name = "test_last_user_churn"
    errors = multiprocessing.Queue()
    procs = [multiprocessing.Process(target=_churn, args=(name, 30, errors)) for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert all(p.exitcode == 0 for p in procs), "A worker crashed or hung"
    assert errors.empty(), f"Worker errors: {errors.get()}"
    time.sleep(0.1)
    leftovers = _files(name)
    assert not leftovers, f"Files left after every worker closed: {leftovers}"
    print("[PASS] test_concurrent_open_close")


def test_no_stale_segment_after_foreign_resize():
    """read()/write() must follow a resize done by another instance at once,
    not only when the listener thread catches up (up to 100 ms later)."""
    for i in range(50):
        a = EasySHM("test_stale_seg", size=256)
        b = EasySHM("test_stale_seg")
        a.write(b"X" * 1000)  # a rotates the data segment
        assert b.read() == b"X" * 1000, f"Stale read on round {i}"
        b.write(b"B", offset=0)
        assert a.read(size=1) == b"B", f"Write lost in the old segment on round {i}"
        b.close()
        a.close()
    assert not _files("test_stale_seg")
    print("[PASS] test_no_stale_segment_after_foreign_resize")


def test_mutex_survives_lock_file_deletion():
    """A process blocked on the mutex while the last user deletes the lock
    file must not end up holding a lock on the dead file (two owners)."""
    import threading
    from easyshm.platform.posix import PosixMutex

    name = f"u{os.getuid()}_test_mtx_unlink"
    first = PosixMutex(name)
    assert first.acquire()

    waiter = PosixMutex(name)  # opens the same (soon deleted) file
    acquired = threading.Event()
    t = threading.Thread(target=lambda: (waiter.acquire(), acquired.set()))
    t.start()
    time.sleep(0.1)

    first.destroy()                # last user deletes the lock file...
    newcomer = PosixMutex(name)    # ...and a new process recreates it
    assert newcomer.acquire(timeout_ms=1000)
    first.release()                # the waiter now gets the lock on the dead file

    assert not acquired.wait(0.3), "Waiter took the lock while the newcomer holds it"
    newcomer.release()
    assert acquired.wait(2), "Waiter never acquired the lock"
    t.join()
    for m in (waiter, newcomer, first):
        m.release()
        m.close()
    newcomer.destroy()
    print("[PASS] test_mutex_survives_lock_file_deletion")


if __name__ == "__main__":
    if sys.platform == "win32":
        print("Skipping: POSIX-specific test (Windows refcounts kernel objects itself).")
        sys.exit(0)
    print(f"Running last-user cleanup tests on {sys.platform}...\n")
    test_last_close_removes_everything()
    test_other_process_keeps_segment_alive()
    test_crashed_process_is_not_counted()
    test_persistent_survives_close()
    test_reopen_after_cleanup()
    test_prefix_name_not_touched()
    test_concurrent_open_close()
    test_no_stale_segment_after_foreign_resize()
    test_mutex_survives_lock_file_deletion()
    print(f"\n{'='*40}\nAll tests passed!")
