import ctypes
from typing import Any
from .base import SHMView


class StructView(SHMView):
    """Maps a ctypes.Structure directly onto the shared buffer."""

    def map_buffer(self, buffer: memoryview, **kwargs) -> Any:
        struct_type = kwargs.get("type")
        offset = kwargs.get("offset", 0)
        
        if struct_type is None or not issubclass(struct_type, ctypes.Structure):
            raise ValueError("[EasySHM] StructView requires a 'type' argument (ctypes.Structure).")
            
        # from_buffer creates a structure that shares the memory
        return struct_type.from_buffer(buffer, offset)
