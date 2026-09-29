from typing import Any, Dict
from .base import SHMView
from .numpy_view import NumPyView
from .struct_view import StructView
from .torch_view import TorchView


class ViewRegistry:
    """Registry for all SHM Views."""
    
    _views: Dict[str, SHMView] = {
        "numpy": NumPyView(),
        "struct": StructView(),
        "torch": TorchView(),
    }

    @classmethod
    def register(cls, name: str, view: SHMView):
        """Register a custom view.
        
        Args:
            name:   A unique identifier for the view.
            view:   An instance of a class inheriting from SHMView.
        """
        cls._views[name] = view

    @classmethod
    def get(cls, name: str) -> SHMView:
        """Get a registered view by name."""
        if name not in cls._views:
            raise KeyError(f"[EasySHM] No view registered with name '{name}'.")
        return cls._views[name]


__all__ = ["ViewRegistry", "SHMView"]
