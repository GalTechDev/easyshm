import time
import os
import sys
from easyshm import EasySHM

def test_manual_shrink():
    name = "test_shrink_manual"
    shm = EasySHM(name, size=1024)
    try:
        # Grow
        shm.resize(2048)
        assert shm.capacity == 2048
        
        # Shrink
        shm.resize(512)
        assert shm.capacity == 512
        
        # Write data and check it persists after shrink
        shm.write(b"hello")
        shm.resize(128)
        assert shm.read(5) == b"hello"
        assert shm.capacity == 128
    finally:
        shm.destroy()

def test_auto_shrink():
    name = "test_shrink_auto"
    # Create with auto_shrink=True, initial size 1024
    shm = EasySHM(name, size=1024, auto_shrink=True)
    try:
        # 1. Grow to >= 3000 (by writing 3000 bytes)
        shm.write(b"A" * 3000)
        assert shm.capacity >= 3000
        
        cap_after_grow = shm.capacity
        print(f"Capacity after grow: {cap_after_grow}")
        
        # 2. Write small data with truncate=True
        # Threshold is 25%. 5 / 4096 is way below.
        shm.write(b"small", truncate=True)
        
        # Should have shrunk to max(1024, cap_after_grow // 2)
        # If cap was 4096, should be 2048.
        final_cap = shm.capacity
        print(f"Capacity after shrink: {final_cap}")
        assert final_cap < cap_after_grow
        assert final_cap >= 1024
        
    finally:
        shm.destroy()

if __name__ == "__main__":
    try:
        print("Running manual shrink test...")
        test_manual_shrink()
        print("Manual shrink test passed!")
        
        print("Running auto shrink test...")
        test_auto_shrink()
        print("Auto shrink test passed!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
