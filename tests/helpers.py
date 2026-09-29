"""
Shared helpers for the test scripts.
Importing this module makes the local `easyshm` package importable, so every
test runs with a plain `python tests/test_xxx.py` (no PYTHONPATH needed).
"""

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def shm_dir():
    return "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()


def full_name(name):
    """Name used on disk: POSIX segments are prefixed with the user id."""
    if sys.platform != "win32" and hasattr(os, "getuid"):
        return f"u{os.getuid()}_{name}"
    return name


def shm_file(name, suffix):
    """Backing file of a segment part, e.g. shm_file("x", "ctrl") or shm_file("x", "d0")."""
    return os.path.join(shm_dir(), f"easyshm_{full_name(name)}_{suffix}")


def child_env():
    """Environment for subprocesses so they import the local package too."""
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return env


def require_easysync():
    """Make `easysync` importable (installed, or from $EASYSYNC_PATH), else skip the test."""
    extra = os.environ.get("EASYSYNC_PATH")
    if extra and extra not in sys.path:
        sys.path.insert(0, extra)
    try:
        import easysync  # noqa: F401
    except ImportError:
        print("SKIP: easysync is not installed (set EASYSYNC_PATH to its source folder)")
        sys.exit(0)
    return extra
