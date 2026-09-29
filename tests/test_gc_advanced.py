import os
import sys
import unittest
from unittest.mock import patch
import helpers  # noqa: F401  (makes the local package importable)
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
        
        # A resize creates an orphan segment, which triggers a forced cleanup
        # (plain writes that do not resize skip the scan on purpose)
        shm.write(b"x" * 10_000)
        
        if sys.platform != "win32":
            self.assertGreater(shm._last_gc_time, initial_gc_time)
        shm.destroy()

if __name__ == "__main__":
    unittest.main()
