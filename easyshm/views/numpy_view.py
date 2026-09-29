from typing import Any
from .base import SHMView


class NumPyView(SHMView):
    """Maps a NumPy array directly onto the shared buffer."""

    def map_buffer(self, buffer: memoryview, **kwargs) -> Any:
        import numpy as np
        shape = kwargs.get("shape")
        dtype = kwargs.get("dtype", "uint8")
        offset = kwargs.get("offset", 0)
        
        if shape is None:
            raise ValueError("[EasySHM] NumPyView requires a 'shape' argument.")
            
        dt = np.dtype(dtype)
        # Map the NumPy array directly onto the buffer
        return np.ndarray(shape, dtype=dt, buffer=buffer, offset=offset)
