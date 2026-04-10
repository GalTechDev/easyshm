from abc import ABC, abstractmethod
from typing import Any


class SHMView(ABC):
    """Base class for all Shared Memory Views.
    A view provides a typed interface over a raw memory buffer without copying.
    """

    @abstractmethod
    def map_buffer(self, buffer: memoryview, **kwargs) -> Any:
        """Map the view onto the provided memoryview.
        
        Args:
            buffer: The raw mmap-backed memoryview.
            kwargs: View-specific configuration (shape, dtype, etc.)
            
        Returns:
            The mapped object (e.g., np.ndarray, ctypes.Structure, etc.)
        """
        pass
