import time
import multiprocessing
import os
import sys

# Ensure we can import easyshm
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from easyshm import EasySHM

def ping_process(name, ready_event, iterations=1000):
    """Wait for signal, then signal back."""
    shm = EasySHM(name)
    ready_event.set()
    
    for _ in range(iterations):
        shm.wait_update()
        shm.write(b"pong")

def pong_process(name, ready_event, iterations=1000):
    """Signal, then wait for signal back. Measure time."""
    shm = EasySHM(name)
    ready_event.wait()
    
    latencies = []
    
    print(f"Starting benchmark: {iterations} iterations...")
    for i in range(iterations):
        t0 = time.perf_counter()
        shm.write(b"ping")
        shm.wait_update()
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000) # ms
        
    avg = sum(latencies) / len(latencies)
    print(f"Average Latency (Round-trip): {avg:.4f} ms")
    print(f"Min Latency: {min(latencies):.4f} ms")
    print(f"Max Latency: {max(latencies):.4f} ms")

if __name__ == "__main__":
    NAME = "bench_raw"
    ITER = 1000
    
    # Cleanup
    with EasySHM(NAME) as s:
        s.destroy()
        
    ready = multiprocessing.Event()
    
    p1 = multiprocessing.Process(target=ping_process, args=(NAME, ready, ITER))
    p2 = multiprocessing.Process(target=pong_process, args=(NAME, ready, ITER))
    
    p1.start()
    p2.start()
    
    p1.join()
    p2.join()
