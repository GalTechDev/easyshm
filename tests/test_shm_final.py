import multiprocessing
import time
import sys
import os

from helpers import require_easysync
require_easysync()
from easysync import shm_connect, SyncedObject

@SyncedObject()
class GameState:
    def __init__(self):
        self.score = 0
        self.player = "nobody"

def process_writer(ready_event, done_event):
    client = shm_connect("final_demo")
    state = GameState()
    ready_event.set()
    time.sleep(1)
    
    state.score = 42
    time.sleep(0.5)
    state.player = "Alice"
    time.sleep(0.5)
    state.score = 100
    
    done_event.wait(timeout=10)
    client.close()

def process_reader(ready_event, done_event):
    ready_event.wait(timeout=5)
    time.sleep(0.2)
    client = shm_connect("final_demo")
    state = GameState()
    
    log = []
    for i in range(10):
        time.sleep(0.4)
        s = object.__getattribute__(state, "score")
        p = object.__getattribute__(state, "player")
        log.append(f"Poll {i}: {s}, {p}")
        if s == 100 and p == "Alice": break
            
    with open("test_results.txt", "w") as f:
        f.write("\n".join(log))
        if "100, Alice" in log[-1]:
            f.write("\nSUCCESS")
        else:
            f.write("\nFAIL")
    
    done_event.set()
    client.close()

if __name__ == "__main__":
    from easyshm import EasySHM
    try: EasySHM("easysync_final_demo_bus").destroy()
    except: pass

    ready = multiprocessing.Event()
    done = multiprocessing.Event()
    p1 = multiprocessing.Process(target=process_writer, args=(ready, done))
    p2 = multiprocessing.Process(target=process_reader, args=(ready, done))
    p1.start(); p2.start()
    p1.join(timeout=10); p2.join(timeout=10)
    
    with open("test_results.txt", "r") as f:
        results = f.read()
    os.unlink("test_results.txt")
    print(results)
    if not results.endswith("SUCCESS"):
        sys.exit(1)
