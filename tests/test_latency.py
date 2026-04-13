import os
import sys
import time
import subprocess
import threading
from easyshm import EasySHM

def test_signal_latency():
    name = "test_latency"
    # Clean start
    try:
        EasySHM(name).destroy()
    except:
        pass

    print("Measuring signaling latency between two processes...")
    
    # We'll use a result file to get the timestamp from the receiver
    result_file = "latency_result.tmp"
    if os.path.exists(result_file): os.unlink(result_file)

    receiver_code = f"""
import time
import os
from easyshm import EasySHM
s = EasySHM('{name}')
def on_update():
    t = time.perf_counter()
    with open('{result_file}', 'w') as f:
        f.write(str(t))
    os._exit(0)

s.on_update(on_update)
# Signal that we are ready
with open('{result_file}.ready', 'w') as f: f.write('ready')

# Wait for signal (background thread will handle it)
time.sleep(10)
"""
    
    p = subprocess.Popen([sys.executable, "-c", receiver_code], env=os.environ.copy())
    
    # Wait for receiver to be ready
    for _ in range(50):
        if os.path.exists(result_file + ".ready"): break
        time.sleep(0.1)
    
    if not os.path.exists(result_file + ".ready"):
        p.kill()
        raise Exception("Receiver failed to start")
    
    time.sleep(0.2) # Extra buffer
    
    shm = EasySHM(name)
    t_start = time.perf_counter()
    shm.write(b"PING")
    
    # Wait for result file
    t_end = None
    for _ in range(100):
        if os.path.exists(result_file):
            with open(result_file, 'r') as f:
                t_end = float(f.read())
            break
        time.sleep(0.01)
        
    p.wait()
    
    if t_end:
        latency_ms = (t_end - t_start) * 1000
        print(f"Latency: {latency_ms:.3f} ms")
    else:
        print("FAILED: No signal received within timeout")
        
    # Cleanup
    shm.destroy()
    if os.path.exists(result_file): os.unlink(result_file)
    if os.path.exists(result_file + ".ready"): os.unlink(result_file + ".ready")

if __name__ == "__main__":
    test_signal_latency()
