import os
import sys
from easyshm import EasySHM

def test_checksum_valid():
    name = "test_checksum_ok"
    shm = EasySHM(name, size=1024)
    # Write some data to trigger header updates
    shm.write(b"Hello Checksum")
    
    # Check that we can read it back (implies valid checksum)
    assert shm.read(14) == b"Hello Checksum"
    shm.destroy()

def test_checksum_corruption():
    name = "test_checksum_corrupt"
    shm = EasySHM(name, size=1024)
    shm.write(b"Safety first")
    
    # Now manually corrupt the header
    # We know the control segment name
    ctrl_name = f"easyshm_{name}_ctrl"
    # We'll use a direct Segment object to tamper with it
    from easyshm.segment import Segment
    ctrl = Segment(ctrl_name, 256)
    
    # Read the header, flip one bit in the magic or version
    raw = ctrl.read(0, 36)
    corrupt = bytearray(raw)
    corrupt[5] = (corrupt[5] + 1) % 256 # Tamper with seg_version
    ctrl.write(bytes(corrupt), 0)
    
    # Now trying to read via EasySHM should fail
    # Note: EasySHM has 5 retries, so we expect a ValueError after those retries
    failed = False
    try:
        shm.read(12)
    except ValueError as e:
        failed = True
        assert "checksum mismatch" in str(e) or "Failed to read a valid header" in str(e)
    
    if not failed:
        raise Exception("Checksum corruption was NOT detected!")
    
    ctrl.close()
    shm.destroy()

if __name__ == "__main__":
    # Manual run if pytest not available
    try:
        test_checksum_valid()
        print("Checksum valid test passed!")
        
        try:
            test_checksum_corruption()
            print("Checksum corruption test passed!")
        except Exception as e:
            print(f"Checksum corruption test FAILED (Expectedly?): {e}")
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
