from typing import Any
from .base import SHMView


class TorchView(SHMView):
    """Maps a PyTorch Tensor directly onto the shared buffer (CPU only)."""

    def map_buffer(self, buffer: memoryview, **kwargs) -> Any:
        import torch
        import numpy as np
        
        shape = kwargs.get("shape")
        dtype = kwargs.get("dtype", "float32")
        offset = kwargs.get("offset", 0)
        
        if shape is None:
            raise ValueError("[EasySHM] TorchView requires a 'shape' argument.")
            
        # We leverage NumPy's buffer mapping then convert to Torch (zero-copy)
        dt = np.dtype(dtype)
        # Using np.ndarray directly on buffer
        arr = np.ndarray(shape, dtype=dt, buffer=buffer, offset=offset)
        return torch.from_numpy(arr)
