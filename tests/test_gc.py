import os
import sys
import time
import glob
import subprocess
import tempfile
from easyshm import EasySHM

def test_gc_logic():
    if sys.platform == "win32":
        print("Skipping detailed GC test on Windows (auto-cleaned by OS).")
        # Just check it doesn't crash
        shm = EasySHM("test_gc_win", size=1024)
        shm.resize(2048)
        shm.destroy()
        return

    name = "test_gc_posix"
    # Ensure clean start
    dir_shm = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
    for f in glob.glob(os.path.join(dir_shm, f"easyshm_{name}_*")):
        try: os.unlink(f)
        except: pass

    print("Creating segment and resizing...")
    shm = EasySHM(name, size=1024)
    shm.write(b"DATA")
    
    # Resize creates v1, old is v0
    shm.resize(2048)
    
    # Check that v0 is still there if we can't delete it? 
    # Actually, right now nobody else is using v0, so resize calls _cleanup_orphans
    # which SHOULD delete v0 immediately.
    
    v0_path = os.path.join(dir_shm, f"easyshm_{name}_d0")
    if os.path.exists(v0_path):
        print("Error: v0 should have been cleaned up as it's unused!")
        assert not os.path.exists(v0_path)
    else:
        print("v0 was successfully cleaned up.")

    # Simulate an orphan that IS in use
    # We launch a subprocess that keeps v1 open
    code = f"import sys; sys.path.append('.'); from easyshm import EasySHM; import time; s=EasySHM('{name}'); time.sleep(2)"
    p = subprocess.Popen([sys.executable, "-c", code], env=os.environ.copy())
    time.sleep(0.5) # Let it open
    
    # Now we resize again (v1 -> v2)
    shm.resize(4096)
    
    # v1 should STILL be there because the subprocess is holding it
    v1_path = os.path.join(dir_shm, f"easyshm_{name}_d1")
    if os.path.exists(v1_path):
        print("v1 is still there (correctly) because subprocess is using it.")
    else:
        print("Error: v1 was deleted while in use!")
        assert os.path.exists(v1_path)
        
    p.wait() # Wait for subprocess to finish (releases v1)
    
    # Now trigger another cleanup (e.g. by another resize or init)
    shm.resize(8192)
    
    if not os.path.exists(v1_path):
        print("v1 was successfully cleaned up after subprocess finished.")
    else:
        print("Error: v1 was NOT cleaned up after use!")
        assert not os.path.exists(v1_path)

    shm.destroy()

if __name__ == "__main__":
    try:
        test_gc_logic()
        print("GC Logic test passed!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
