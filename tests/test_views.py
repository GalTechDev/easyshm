import unittest
import numpy as np
import ctypes
import os
import sys
import time

# Ensure we can import easyshm
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from easyshm import EasySHM

class TestViews(unittest.TestCase):
    def setUp(self):
        self.name = "test_views"
        with EasySHM(self.name) as s:
            s.destroy()

    def test_numpy_view(self):
        with EasySHM(self.name, size=1024) as shm:
            arr = shm.as_view("numpy", shape=(10,), dtype="float32")
            arr[0] = 3.14
            self.assertEqual(arr[0], np.float32(3.14))
            del arr # Release the buffer pointer before closing shm

    def test_struct_view(self):
        class MyStruct(ctypes.Structure):
            _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_float)]

        with EasySHM(self.name, size=1024) as shm:
            s = shm.as_view("struct", type=MyStruct)
            s.x = 42
            s.y = 123.456
            self.assertEqual(s.x, 42)
            del s # Release the buffer pointer before closing shm

    def test_torch_view(self):
        try:
            import torch
        except ImportError:
            self.skipTest("PyTorch not installed")

        with EasySHM(self.name, size=1024) as shm:
            t = shm.as_view("torch", shape=(5,), dtype="float32")
            t[0] = 99.9
            self.assertEqual(t[0], 99.9)
            del t # Release the buffer pointer before closing shm


    def test_invalid_view(self):
        with EasySHM(self.name, size=1024) as shm:
            with self.assertRaises(KeyError):
                shm.as_view("non_existent_view")

if __name__ == "__main__":
    unittest.main()
