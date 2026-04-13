import os
import sys
import time
import subprocess
import signal
from easyshm import EasySHM, LockAbandonedError

def test_mutex_abandonment_recovery():
    name = "test_mutex_abort"
    # Ensure clean start
    try:
        shm = EasySHM(name)
        shm.destroy()
    except:
        pass

    print("Launching locker process that will crash...")
    # This script acquires the lock and then kills itself without releasing
    # We use a file to coordinate so we know it has the lock
    sync_file = "mutex_sync.tmp"
    if os.path.exists(sync_file): os.unlink(sync_file)
    
    code = f"""
import sys
import os
import time
from easyshm import EasySHM
try:
    s = EasySHM('{name}')
    with s._ipc_lock:
        with open('{sync_file}', 'w') as f: f.write('locked')
        # Wait to be killed
        time.sleep(10)
except Exception as e:
    with open('{sync_file}', 'w') as f: f.write(str(e))
"""
    p = subprocess.Popen([sys.executable, "-c", code], env=os.environ.copy())
    
    # Wait for it to have the lock
    for _ in range(50):
        if os.path.exists(sync_file): break
        time.sleep(0.1)
    
    if not os.path.exists(sync_file):
        p.kill()
        raise Exception("Subprocess failed to start or lock")
        
    print("Process has the lock. Killing it now.")
    p.kill() # Hard kill
    p.wait()
    time.sleep(0.5) # Let OS cleanup handles
    
    print("Attempting to acquire abandoned lock with auto_recover=True (default)...")
    try:
        shm2 = EasySHM(name, auto_recover_mutex=True)
        # Should succeed!
        shm2.write(b"RECOVERED")
        assert shm2.read(9) == b"RECOVERED"
        print("Success: Abandoned lock recovered automatically.")
        shm2.destroy()
    except Exception as e:
        print(f"FAILED: Could not recover abandoned lock: {e}")
        raise

    # Test with auto_recover=False
    print("\nTesting with auto_recover=False...")
    # Re-create the scenario
    if os.path.exists(sync_file): os.unlink(sync_file)
    p = subprocess.Popen([sys.executable, "-c", code], env=os.environ.copy())
    for _ in range(50):
        if os.path.exists(sync_file): break
        time.sleep(0.1)
    p.kill()
    p.wait()
    time.sleep(0.5)
    
    try:
        print("Attempting to acquire with auto_recover=False...")
        shm3 = EasySHM(name, auto_recover_mutex=False)
        print("FAILED: Should have raised LockAbandonedError!")
        shm3.destroy()
        assert False, "LockAbandonedError not raised"
    except LockAbandonedError:
        print("Success: LockAbandonedError correctly raised.")
    except Exception as e:
        print(f"FAILED: Wrong exception raised: {type(e).__name__}: {e}")
        raise
    finally:
        if os.path.exists(sync_file): os.unlink(sync_file)
        # Final cleanup - need a recovery=True shm to destroy
        try:
            final = EasySHM(name, auto_recover_mutex=True)
            final.destroy()
        except: pass

if __name__ == "__main__":
    try:
        test_mutex_abandonment_recovery()
        print("\nAll Mutex robustness tests passed!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
