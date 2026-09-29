"""
Test: EasySync + EasySHM Integration (Final Robustness)
=======================================================
Two processes share a @SyncedObject through RAM.
No sockets. No servers. Zero-latency.
"""

import multiprocessing
import time
import sys
import os

from helpers import require_easysync
require_easysync()

from easysync import shm_connect, SyncedObject

# CRITICAL: Define class at global scope so __qualname__ is the same ("GameState")
# in both processes.
@SyncedObject()
class GameState:
    def __init__(self):
        self.score = 0
        self.player = "nobody"

def process_writer(ready_event, done_event):
    """Process A: Modify the synchronized object."""
    # Connect to the cluster
    client = shm_connect("integ_final")
    
    state = GameState()
    
    print("[Writer] Ready. Signalling Reader...")
    ready_event.set()
    time.sleep(1) # Let reader initialize

    print("[Writer] Updating score -> 42")
    state.score = 42
    time.sleep(0.5)

    print("[Writer] Updating player -> 'Alice'")
    state.player = "Alice"
    time.sleep(0.5)

    print("[Writer] Updating score -> 100")
    state.score = 100
    
    # Wait for reader to confirm receipt
    print("[Writer] Waiting for reader to finish...")
    done_event.wait(timeout=10)
    print("[Writer] Finished.")
    client.close()

def process_reader(ready_event, done_event):
    """Process B: Observe the synchronized object."""
    print("[Reader] Waiting for Writer...")
    ready_event.wait(timeout=5)
    
    # Small delay to ensure writer is fully initialized
    time.sleep(0.2)
    
    # Connect to the SAME cluster
    client = shm_connect("integ_final")
    
    state = GameState()
    
    print("[Reader] Listening for changes...")
    
    # We will poll the state 10 times with short sleeps
    success = False
    for i in range(10):
        time.sleep(0.5)
        # Using object.__getattribute__ to be extra sure we see the real current values
        s = object.__getattribute__(state, "score")
        p = object.__getattribute__(state, "player")
        print(f"[Reader] Poll {i}: score={s}, player={p}")
        
        if s == 100 and p == "Alice":
            success = True
            break
            
    if success:
        print("[Reader] ✅ SUCCESS: State synchronized via Shared Memory!")
    else:
        print("[Reader] ❌ FAIL: Updates not received.")

    done_event.set()
    client.close()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    # Cleanup previous runs
    from easyshm import EasySHM
    try:
        EasySHM("easysync_integ_final_bus").destroy()
    except:
        pass

    print("=== STARTING FINAL INTEGRATION TEST ===")
    
    ready = multiprocessing.Event()
    done = multiprocessing.Event()
    
    p1 = multiprocessing.Process(target=process_writer, args=(ready, done))
    p2 = multiprocessing.Process(target=process_reader, args=(ready, done))
    
    p1.start()
    p2.start()
    
    p1.join(timeout=15)
    p2.join(timeout=15)
    
    print("=== TEST COMPLETED ===")
    
    if p1.is_alive(): p1.terminate()
    if p2.is_alive(): p2.terminate()
    if p2.exitcode != 0:
        sys.exit(1)
