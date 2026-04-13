import os
import sys
import unittest
import struct
import zlib
from easyshm import EasySHM, ProtocolIncompatibilityError

class TestProtocolIncompatibility(unittest.TestCase):
    def test_version_mismatch(self):
        name = "test_proto_mismatch"
        
        # 1. Start clean
        try:
            EasySHM(name).destroy()
        except:
            pass

        # 2. Simulate an "old" segment with protocol version 99 (future/incompatible)
        # We need to write the header manually because the library won't let us write 99
        ctrl_name = f"easyshm_{name}_ctrl"
        from easyshm.segment import Segment
        ctrl = Segment(ctrl_name, 256)
        
        # Header Layout: <4sIQQIIII
        # Magic, seg_v, size, cap, seq, flags, proto_v, checksum
        MAGIC = b"ESHM"
        format = "<4sIQQIIII"
        proto_v = 99
        raw_no_ck = struct.pack(format, MAGIC, 0, 0, 1024, 0, 0, proto_v, 0)
        checksum = zlib.crc32(raw_no_ck[:-4]) & 0xFFFFFFFF
        raw_final = struct.pack(format, MAGIC, 0, 0, 1024, 0, 0, proto_v, checksum)
        
        ctrl.write(raw_final, 0)
        # DO NOT close() here on Windows, or the paging-file-backed segment will vanish
        # ctrl.close()

        # 3. Try to join with the current library (which expects v1)
        print("\nAttempting to join segment with Protocol Version 99...")
        try:
            shm = EasySHM(name)
            self.fail("Should have raised ProtocolIncompatibilityError")
        except ProtocolIncompatibilityError as e:
            print(f"Success: Caught expected error: {e}")
            self.assertIn("Incompatible protocol version", str(e))
        except Exception as e:
            self.fail(f"Caught wrong exception: {type(e).__name__}: {e}")
        finally:
            # Cleanup
            ctrl.close() # Now we can close
            try:
                # We need to manually destroy since EasySHM(name) fails
                from easyshm.platform import Signal
                Signal(f"{name}_sig").destroy()
                ctrl = Segment(ctrl_name, 256)
                ctrl.destroy()
                # Also the data segment d0
                data = Segment(f"easyshm_{name}_d0", 1024)
                data.destroy()
            except:
                pass

if __name__ == "__main__":
    unittest.main()
