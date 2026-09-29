"""
Run every test script and print a summary.

    python tests/run_all.py
    EASYSYNC_PATH=/path/to/easysync python tests/run_all.py   # include EasySync integration

Each test_*.py file runs in its own process (they use multiprocessing and
named OS objects), with a timeout. Exit code is 1 if any file fails.
"""

import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 300


def main():
    files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    failed = []
    for path in files:
        name = os.path.basename(path)
        t0 = time.time()
        try:
            proc = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=TIMEOUT)
            out = (proc.stdout + proc.stderr).strip()
            status = "SKIP" if proc.returncode == 0 and "SKIP:" in out else ("PASS" if proc.returncode == 0 else "FAIL")
        except subprocess.TimeoutExpired as e:
            out, status = f"timeout after {TIMEOUT}s\n{e.stdout or ''}", "FAIL"
        print(f"[{status}] {name} ({time.time() - t0:.1f}s)")
        if status == "FAIL":
            failed.append(name)
            print("    " + "\n    ".join(out.splitlines()[-15:]))
    print(f"\n{len(files) - len(failed)}/{len(files)} files passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
