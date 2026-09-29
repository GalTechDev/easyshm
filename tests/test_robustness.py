import os
import sys
import time
import subprocess
import tempfile

from helpers import child_env, shm_file
from easyshm import EasySHM

def test_startup_race():
    name = "test_race_init"
    # Ensure physical files are gone if any
    try:
        EasySHM(name).destroy()
    except:
        pass
        
    print(f"Starting race test for segment '{name}'...")
    # Keep one handle open during the whole test to prevent Windows from 
    # destroying the named mapping when subprocesses close.
    main_shm = EasySHM(name, size=1024)
    
    processes = []
    # Simultaneous initialization of 10 processes
    env = child_env()
    for i in range(10):
        code = f"import sys; from easyshm import EasySHM; s=EasySHM('{name}', size=1024); s.write(b'X', offset={i}); s.close()"
        p = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(p)
    
    for p in processes:
        p.wait()
        if p.returncode != 0:
            out, err = p.communicate()
            print(f"Process failed with return code {p.returncode}")
            print(f"Stderr: {err}")
        assert p.returncode == 0
    
    shm = EasySHM(name)
    print(f"Final capacity: {shm.capacity}, Data: {shm.read(10)}")
    assert shm.capacity >= 1024
    # Check that data from all processes is there
    data = shm.read(10)
    # We might need a small sleep to ensure all async signals were processed 
    # but the subprocesses are already finished (p.wait()).
    assert data == b"XXXXXXXXXX"
    shm.destroy()
    print("Startup race test passed!")

def test_permissions():
    if sys.platform == "win32":
        print("Skipping permissions test on Windows.")
        return
    
    name = "test_perms_0600"
    mode_target = 0o600
    shm = EasySHM(name, mode=mode_target)
    
    # Check backing file in /dev/shm or temp
    ctrl_path = shm_file(name, "ctrl")
    assert os.path.exists(ctrl_path), f"Backing file not found: {ctrl_path}"
    actual_mode = os.stat(ctrl_path).st_mode & 0o777
    print(f"File: {ctrl_path}, Mode: {oct(actual_mode)}")
    assert actual_mode == mode_target, f"Mode {oct(actual_mode)} != {oct(mode_target)}"

    shm.destroy()
    print("Permissions test passed!")

if __name__ == "__main__":
    try:
        test_startup_race()
        test_permissions()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
