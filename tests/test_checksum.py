import os
import sys

from helpers import shm_file
from easyshm import EasySHM, CorruptedSegmentError
from easyshm.segment import Segment

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
    ctrl = Segment(os.path.basename(shm_file(name, "ctrl")), 256)
    
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


def test_join_corrupted_keeps_data():
    """Joining a segment whose header is corrupted must raise, not wipe it."""
    name = "test_checksum_join"
    owner = EasySHM(name, size=1024)
    owner.write(b"precious data")

    ctrl = Segment(os.path.basename(shm_file(name, "ctrl")), 256)
    raw = bytearray(ctrl.read(0, 40))
    raw[5] ^= 0xFF  # corrupt seg_version, checksum no longer matches
    ctrl.write(bytes(raw), 0)

    try:
        EasySHM(name)
        raise AssertionError("Joining a corrupted segment did not raise")
    except CorruptedSegmentError as e:
        assert "EasySHM.unlink" in str(e)

    raw[5] ^= 0xFF  # repair: the data must still be there
    ctrl.write(bytes(raw), 0)
    assert owner.read() == b"precious data", "Data was wiped by the failed join"
    ctrl.close()
    owner.destroy()


if __name__ == "__main__":
    test_checksum_valid()
    print("Checksum valid test passed!")
    test_checksum_corruption()
    print("Checksum corruption test passed!")
    test_join_corrupted_keeps_data()
    print("Corrupted join test passed!")
