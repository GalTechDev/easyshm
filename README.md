# EasySHM 🚀

**High-Performance Shared Memory IPC for Python.**

EasySHM is a zero-latency communication library that allows Python processes to share data directly through RAM.

## Features ✨
- **Zero-Socket IPC**: No open ports, no network overhead.
- **Kernel-Level Signaling**: Uses Win32 Events (Windows) and POSIX Semaphores (Linux) for instant wake-ups.
- **Auto-Grow**: Dynamic memory segment resizing without stopping the system.
- **NumPy Zero-Copy**: Share large arrays at RAM speeds (Giga-octets per second).
- **Thread-Safe & Process-Safe**: Built-in locking mechanism.

## Installation 📦
```bash
pip install py-easyshm
```

## Quick Start ⏱️
```python
from easyshm import EasySHM
import ctypes

# Create or join a shared segment
shm = EasySHM("my_data", size=1024)

# 1. Use it as a NumPy array (Zero-Copy)
arr = shm.as_view("numpy", shape=(10,), dtype='float32')
arr[0] = 42.0

# 2. Use it as a C-Structure (Zero-Copy)
class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int)]

p = shm.as_view("struct", type=Point)
p.x = 100

# 3. Use it as a PyTorch Tensor
import torch
t = shm.as_view("torch", shape=(10,))
```

## Architecture 🛠️
EasySHM is platform-agnostic:
- **Windows**: Uses `CreateFileMapping` and session-scoped Named Events (`Local\`).
- **Linux/Unix**: Uses POSIX `mmap` and Semaphores in `/dev/shm`.

## Advanced Views 🧩
EasySHM now supports an extensible **View Registry**. You can map any typed structure directly over shared bytes without copies.
- `as_view("numpy", shape, dtype)`
- `as_view("struct", type=MyCtypesStruct)`
- `as_view("torch", shape, dtype)`

## Performance ⚡
| Transport | Latency (1MB Sync) | Overhead |
|-----------|--------------------|----------|
| TCP (Socket) | ~5.0ms - 20ms     | High (Network Stack) |
| **EasySHM** | **< 0.1ms**        | **Zero (Direct RAM)** |


---
Part of the **EasySync** ecosystem. Created by GalTechDev.
