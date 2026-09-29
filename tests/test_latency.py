"""
Test: signal latency between two processes
===========================================
Runs a short ping-pong with the benchmark code and checks loose bounds.
The bounds catch regressions (a missed wake-up falls back to 100 ms polling,
a lock stall costs ~5 ms), not small variations: for real numbers run
`python benchmarks/latency.py`.
"""

import os
import sys

from helpers import ROOT

sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
from latency import bench_shm, summarize  # noqa: E402

P50_LIMIT_US = 1_000
P99_LIMIT_US = 20_000


def test_signal_latency():
    one_way = summarize([s / 2 for s in bench_shm(iters=300, warmup=30)])
    print("Signal latency (one-way): "
          + ", ".join(f"{k[:-3]}={one_way[k]:.0f} µs" for k in ("min_us", "p50_us", "p99_us", "max_us")))
    assert one_way["p50_us"] < P50_LIMIT_US, f"Median latency too high: {one_way['p50_us']:.0f} µs"
    assert one_way["p99_us"] < P99_LIMIT_US, f"p99 latency too high: {one_way['p99_us']:.0f} µs"


if __name__ == "__main__":
    test_signal_latency()
    print("Latency test passed!")
