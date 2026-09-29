"""
Tests for EasySHM — Cross-Platform Shared Memory Engine
=========================================================
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easyshm import EasySHM


def test_create_and_write():
    """Test basic segment creation and write/read."""
    shm = EasySHM("test_basic", size=256)
    try:
        shm.write(b"Hello EasySHM!")
        result = shm.read()
        assert result == b"Hello EasySHM!", f"Expected 'Hello EasySHM!', got {result}"
        print("[PASS] test_create_and_write")
    finally:
        shm.destroy()


def test_write_with_offset():
    """Test writing at a specific offset."""
    shm = EasySHM("test_offset", size=256)
    try:
        shm.write(b"AAAA", offset=0)
        shm.write(b"BBBB", offset=4)
        result = shm.read(size=8, offset=0)
        assert result == b"AAAABBBB", f"Expected 'AAAABBBB', got {result}"
        print("[PASS] test_write_with_offset")
    finally:
        shm.destroy()


def test_auto_grow():
    """Test that auto_grow expands the buffer when needed."""
    shm = EasySHM("test_grow", size=64, auto_grow=True)
    try:
        initial_cap = shm.capacity
        # Write more than initial capacity
        big_data = b"X" * 200
        shm.write(big_data)
        assert shm.capacity > initial_cap, "Capacity should have grown"
        result = shm.read()
        assert result == big_data, "Data should be intact after resize"
        print(f"[PASS] test_auto_grow (64 -> {shm.capacity} bytes)")
    finally:
        shm.destroy()


def test_auto_grow_disabled():
    """Test that OverflowError is raised if auto_grow is disabled."""
    shm = EasySHM("test_no_grow", size=64, auto_grow=False)
    try:
        big_data = b"X" * 200
        raised = False
        try:
            shm.write(big_data)
        except OverflowError:
            raised = True
        assert raised, "Should have raised OverflowError"
        print("[PASS] test_auto_grow_disabled")
    finally:
        shm.destroy()


def test_manual_resize():
    """Test manual resize."""
    shm = EasySHM("test_resize", size=64)
    try:
        shm.write(b"before resize")
        shm.resize(1024)
        assert shm.capacity >= 1024, f"Expected capacity >= 1024, got {shm.capacity}"
        result = shm.read(size=13)
        assert result == b"before resize", f"Data lost after resize: {result}"
        print(f"[PASS] test_manual_resize (capacity={shm.capacity})")
    finally:
        shm.destroy()


def test_join_existing():
    """Test joining an already created segment."""
    writer = EasySHM("test_join", size=256)
    try:
        writer.write(b"shared data here")
        time.sleep(0.1)

        reader = EasySHM("test_join")
        try:
            result = reader.read()
            assert result == b"shared data here", f"Reader got: {result}"
            print("[PASS] test_join_existing")
        finally:
            reader.close()
    finally:
        writer.destroy()


def test_signal_notification():
    """Test that the listener thread fires callbacks on write."""
    writer = EasySHM("test_signal", size=256)
    # Give time for the listener to start
    time.sleep(0.1)

    reader = EasySHM("test_signal")
    received = threading.Event()

    def on_data():
        received.set()

    reader.on_update(on_data)
    time.sleep(0.1)

    try:
        writer.write(b"ping!")
        ok = received.wait(timeout=2.0)
        assert ok, "Callback was not triggered within 2 seconds"
        result = reader.read()
        assert result == b"ping!", f"Expected 'ping!', got {result}"
        print("[PASS] test_signal_notification")
    finally:
        reader.close()
        writer.destroy()


def test_numpy_integration():
    """Test zero-copy NumPy array mapping."""
    try:
        import numpy as np
    except ImportError:
        print("[SKIP] test_numpy_integration (numpy not installed)")
        return

    shm = EasySHM("test_numpy", size=4096)
    try:
        arr = shm.as_ndarray(shape=(10, 10), dtype="float32")
        arr[0, 0] = 42.0
        arr[9, 9] = -1.0

        # Re-read from a different EasySHM instance
        reader = EasySHM("test_numpy")
        try:
            arr2 = reader.as_ndarray(shape=(10, 10), dtype="float32")
            assert arr2[0, 0] == 42.0, f"Expected 42.0, got {arr2[0, 0]}"
            assert arr2[9, 9] == -1.0, f"Expected -1.0, got {arr2[9, 9]}"
            print("[PASS] test_numpy_integration (zero-copy)")
        finally:
            reader.close()
    finally:
        shm.destroy()


def test_metadata():
    """Test data_size, capacity and write_seq properties."""
    shm = EasySHM("test_meta", size=256)
    try:
        assert shm.data_size == 0, f"Initial data_size should be 0, got {shm.data_size}"
        shm.write(b"12345")
        assert shm.data_size == 5, f"data_size should be 5, got {shm.data_size}"
        seq1 = shm.write_seq
        shm.write(b"more")
        seq2 = shm.write_seq
        assert seq2 > seq1, f"write_seq should increase: {seq1} -> {seq2}"
        print("[PASS] test_metadata")
    finally:
        shm.destroy()


def test_wait_update_no_missed_writes():
    """A write made between two wait_update() calls must not be missed,
    and an instance must not be woken up by its own writes."""
    a = EasySHM("test_wait_seq", size=256)
    b = EasySHM("test_wait_seq")
    try:
        a.write(b"1")                      # before b waits
        assert b.wait_update(timeout=1.0), "Write made before wait_update() was missed"
        assert not b.wait_update(timeout=0.2), "Same write reported twice"

        b.write(b"own")                    # b's own write
        assert not b.wait_update(timeout=0.2), "Woken up by its own write"

        a.write(b"2")
        b.write(b"3")                      # a's write is still unreported
        assert b.wait_update(timeout=1.0), "Foreign write hidden by a later own write"
        print("[PASS] test_wait_update_no_missed_writes")
    finally:
        b.close()
        a.close()


if __name__ == "__main__":
    print(f"Running EasySHM tests on {sys.platform}...\n")
    test_create_and_write()
    test_write_with_offset()
    test_auto_grow()
    test_auto_grow_disabled()
    test_manual_resize()
    test_join_existing()
    test_signal_notification()
    test_numpy_integration()
    test_metadata()
    test_wait_update_no_missed_writes()
    print(f"\n{'='*40}\nAll tests passed!")
