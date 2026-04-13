"""
EasySHM - Platform Abstraction Layer (Base)
============================================
Abstract interface for cross-platform inter-process signaling.
"""

from abc import ABC, abstractmethod


class Signal(ABC):
    """Abstract cross-platform signal for inter-process notification.

    A Signal allows one process to wake up another without using sockets.
    Implementations use OS kernel primitives (Windows Events, POSIX Semaphores).
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def emit(self):
        """Signal all waiting processes. Non-blocking."""

    @abstractmethod
    def wait(self, timeout_ms: int = None) -> bool:
        """Block until signaled or timeout.

        Args:
            timeout_ms: Max wait time in milliseconds. None = wait forever.

        Returns:
            True if signaled, False if timed out.
        """

    @abstractmethod
    def close(self):
        """Release OS resources (handles, semaphores)."""

    @abstractmethod
    def destroy(self):
        """Release AND remove the named OS object (for cleanup)."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
class Mutex(ABC):
    """Abstract cross-platform mutual exclusion (lock) for inter-process sync.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def acquire(self, timeout_ms: int = None) -> bool | str:
        """Acquire the lock. 
        
        Returns:
            True: Success.
            False: Timeout / Failure.
            "abandoned": Success, but the previous owner crashed (Windows).
        """

    @abstractmethod
    def release(self):
        """Release the lock."""

    @abstractmethod
    def close(self):
        """Release OS resources."""

    def __enter__(self):
        res = self.acquire()
        if res is False:
            raise TimeoutError(f"Could not acquire Mutex '{self.name}'")
        return res # Return the result so caller can check for "abandoned"

    def __exit__(self, *args):
        self.release()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
