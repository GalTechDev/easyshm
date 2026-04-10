import sys
import os
import time
import pickle
import struct

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from easyshm import EasySHM

def bus_monitor():
    cluster = "integ_v2"
    bus_name = f"easysync_{cluster}_bus"
    print(f"[Monitor] Monitoring SHM Bus: {bus_name}")
    
    try:
        shm = EasySHM(bus_name)
    except Exception as e:
        print(f"[Monitor] Error opening bus: {e}")
        return

    last_seq = -1
    _BUS_HEADER = struct.Struct("<I")

    print("[Monitor] Waiting for messages...")
    start_time = time.time()
    while time.time() - start_time < 10:
        seq = shm.write_seq
        if seq != last_seq:
            last_seq = seq
            print(f"[Monitor] New Signal! Seq={seq}")
            
            # Read frame
            header = shm.read(size=4, offset=0)
            if len(header) == 4:
                msg_len = _BUS_HEADER.unpack(header)[0]
                if msg_len > 0:
                    raw = shm.read(size=msg_len, offset=4)
                    try:
                        msg = pickle.loads(raw)
                        print(f"  Message: {msg}")
                    except:
                        print("  Error unpickling")
        time.sleep(0.1)
    
    shm.close()

if __name__ == "__main__":
    bus_monitor()
