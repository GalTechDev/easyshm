import os
import sys
import unittest
from unittest.mock import patch
from easyshm import EasySHM

class TestGCAndIsolation(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "UID isolation is POSIX only")
    def test_uid_isolation_name(self):
        name = "test_iso"
        with patch("os.getuid", return_value=1234):
            shm = EasySHM(name)
            # Internal check: full name should contain u1234_
            self.assertIn("u1234_", shm._ipc_lock.name)
            shm.destroy()

    def test_gc_cooldown_logic(self):
        # Even on Windows, the cooldown logic should be present in the object state
        shm = EasySHM("test_cooldown")
        initial_gc_time = shm._last_gc_time
        
        # Trigger an automatic cleanup (not forced)
        shm._cleanup_orphans(force=False)
        
        # Trigger a forced cleanup
        shm.write(b"data") # This calls force=True
        
        if sys.platform != "win32":
            self.assertGreater(shm._last_gc_time, initial_gc_time)
        shm.destroy()

if __name__ == "__main__":
    unittest.main()
