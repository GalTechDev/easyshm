# EasySHM
# EasySHM

**High-Performance Shared Memory IPC for Python.**

EasySHM is a low-latency communication library that allows Python processes to share data directly through RAM.

## Features
## Features
- **Zero-Socket IPC**: No open ports, no network overhead.
- **Kernel-Level Signaling**: Uses a futex (Linux), POSIX semaphores (macOS) or Win32 Events (Windows) to wake every waiting process at once.
- **Auto-Grow**: Dynamic memory segment resizing without stopping the system.
- **NumPy Zero-Copy**: Share large arrays at RAM speeds (Giga-octets per second).
- **Thread-Safe & Process-Safe**: Built-in locking mechanism.

## Installation
## Installation
```bash
pip install py-easyshm
```

## Quick Start
## Quick Start
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

## Architecture
## Architecture
EasySHM is platform-agnostic:
- **Windows**: Uses `CreateFileMapping` and session-scoped Named Events (`Local\`).
- **Linux/Unix**: Uses POSIX `mmap` and Semaphores in `/dev/shm`.

## Advanced Views
## Advanced Views
EasySHM now supports an extensible **View Registry**. You can map any typed structure directly over shared bytes without copies.
- `as_view("numpy", shape, dtype)`
- `as_view("struct", type=MyCtypesStruct)`
- `as_view("torch", shape, dtype)`

## Performance
Measured with `python benchmarks/latency.py` (two processes, thousands of round trips; Linux/WSL2, Python 3.12):

| Measure | EasySHM | TCP localhost |
|---------|---------|---------------|
| Wake-up latency, one-way (median / p99) | 35 µs / 130 µs | 17 µs / 80 µs |
| 1 MB round trip (median) | ~170–190 µs | ~110–160 µs |
| Idle CPU with an open segment | 0 % | 0 % |

Latency is dominated by Python itself, so a localhost socket is still faster for tiny messages. EasySHM is useful when several processes share the same (large) state without resending it, with no port to open. Run the benchmark on your own machine for real numbers.


---
Part of the **EasySync** ecosystem. Created by GalTechDev.
