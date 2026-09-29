"""
EasySHM latency & throughput benchmark
======================================
Measures, between two real processes:

  1. Signal latency: ping-pong of a 4-byte message. One-way latency is half
     of the measured round trip (write -> kernel wake-up -> read -> reply).
  2. Payload round trip: the sender writes N bytes, the receiver wakes up,
     copies them out with read() and acknowledges. "Zero-copy" does the same
     with NumPy views: the sender fills the shared buffer in place (one copy)
     and the receiver uses it in place (no copy), which is how EasySHM is
     meant to move large data.
  3. The same two measurements over a localhost TCP socket (TCP_NODELAY),
     using the exact same protocol, as a baseline.
  4. Idle CPU usage of a process that keeps a segment open.

Usage:
    python benchmarks/latency.py              # default run
    python benchmarks/latency.py --iters 5000 --json results.json
"""

import argparse
import json
import multiprocessing as mp
import os
import platform
import socket
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from easyshm import EasySHM  # noqa: E402

PAYLOAD_SIZES = [64, 64 * 1024, 1024 * 1024, 16 * 1024 * 1024]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def percentile(sorted_values, p):
    k = (len(sorted_values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def summarize(samples_ns):
    s = sorted(samples_ns)
    us = lambda v: v / 1000  # noqa: E731
    return {
        "n": len(s),
        "min_us": us(s[0]),
        "p50_us": us(percentile(s, 50)),
        "p90_us": us(percentile(s, 90)),
        "p99_us": us(percentile(s, 99)),
        "max_us": us(s[-1]),
    }


# ----------------------------------------------------------------------
# EasySHM
# ----------------------------------------------------------------------

def _shm_echo(name, rounds, size, mode, ready):
    """Receiver: wait for each message, consume it, reply on the ack segment."""
    data = EasySHM(name)
    ack = EasySHM(name + "_ack")
    view = data.as_view("numpy", shape=(size,), dtype="uint8") if mode == "view" else None
    ready.set()
    for _ in range(rounds):
        if not data.wait_update(timeout=10):
            break
        if mode == "copy":
            data.read()  # copy the payload out, like a socket consumer
        elif mode == "view":
            view[-1]  # the payload is usable in place, nothing to copy
        ack.write(b"k", truncate=True)
    data.close()
    ack.close()


def bench_shm(iters, warmup, size=0, zero_copy=False):
    ctx = mp.get_context("spawn")
    mode = "view" if zero_copy else ("copy" if size else "signal")
    name = f"bench_{os.getpid()}_{size}_{mode}"
    data = EasySHM(name, size=max(size, 4096))  # sized up front: no resize, views stay valid
    ack = EasySHM(name + "_ack", size=64)
    msg = os.urandom(size) if size else b"ping"
    if zero_copy:
        import numpy as np
        src = np.frombuffer(msg, dtype=np.uint8)
        view = data.as_view("numpy", shape=(size,), dtype="uint8")
    ready = ctx.Event()
    p = ctx.Process(target=_shm_echo, args=(name, iters + warmup, size, mode, ready))
    p.start()
    ready.wait(30)

    samples = []
    for i in range(iters + warmup):
        t0 = time.perf_counter_ns()
        if zero_copy:
            view[:] = src               # fill the shared buffer in place
            data.write(b"", offset=0)   # publish: bump write_seq and wake the receiver
        else:
            data.write(msg, truncate=True)
        if not ack.wait_update(timeout=10):
            raise RuntimeError("EasySHM benchmark: no reply within 10 s")
        t1 = time.perf_counter_ns()
        if i >= warmup:
            samples.append(t1 - t0)
    p.join(30)
    if zero_copy:
        del view  # release the export on the mapping before closing
    data.close()
    ack.close()
    return samples


# ----------------------------------------------------------------------
# TCP baseline (same protocol: message -> 1-byte ack)
# ----------------------------------------------------------------------

def _recv_exact(sock, n, buf):
    view = memoryview(buf)[:n]
    got = 0
    while got < n:
        r = sock.recv_into(view[got:], n - got)
        if not r:
            raise ConnectionError("peer closed")
        got += r


def _tcp_echo(port_queue, rounds, size):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_queue.put(srv.getsockname()[1])
    conn, _ = srv.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    buf = bytearray(max(size, 4))
    for _ in range(rounds):
        _recv_exact(conn, max(size, 4), buf)
        conn.sendall(b"k")
    conn.close()
    srv.close()


def bench_tcp(iters, warmup, size=0):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_tcp_echo, args=(q, iters + warmup, size))
    p.start()
    port = q.get(timeout=30)
    sock = socket.create_connection(("127.0.0.1", port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    msg = os.urandom(size) if size else b"ping"
    ack = bytearray(1)

    samples = []
    for i in range(iters + warmup):
        t0 = time.perf_counter_ns()
        sock.sendall(msg)
        _recv_exact(sock, 1, ack)
        t1 = time.perf_counter_ns()
        if i >= warmup:
            samples.append(t1 - t0)
    sock.close()
    p.join(30)
    return samples


# ----------------------------------------------------------------------
# Idle CPU
# ----------------------------------------------------------------------

def idle_cpu_percent(seconds=3.0):
    """CPU used by this process while it only keeps a segment open."""
    shm = EasySHM(f"bench_idle_{os.getpid()}")
    time.sleep(0.2)
    c0, t0 = time.process_time(), time.perf_counter()
    time.sleep(seconds)
    c1, t1 = time.process_time(), time.perf_counter()
    shm.close()
    return 100 * (c1 - c0) / (t1 - t0)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def human(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:g} {unit}"
        n /= 1024
    return f"{n:g} GB"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iters", type=int, default=2000, help="measured round trips for the signal test")
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--json", help="also write raw results to this file")
    args = ap.parse_args()

    results = {
        "platform": f"{platform.system()} {platform.release()} / Python {platform.python_version()}",
        "cpu_count": os.cpu_count(),
        "signal": {},
        "payload": [],
    }
    print(f"# EasySHM benchmark — {results['platform']}, {os.cpu_count()} CPU\n")

    # 1. Signal latency
    print("## Signal latency (4-byte ping-pong, one-way = round trip / 2)\n")
    print("| Transport | min | p50 | p90 | p99 | max |")
    print("|---|---|---|---|---|---|")
    for label, fn in (("EasySHM", bench_shm), ("TCP localhost", bench_tcp)):
        rt = fn(args.iters, args.warmup)
        one_way = summarize([s / 2 for s in rt])
        results["signal"][label] = one_way
        print(f"| {label} | " + " | ".join(f"{one_way[k]:.1f} µs" for k in ("min_us", "p50_us", "p90_us", "p99_us", "max_us")) + " |")

    # 2. Payload round trip
    print("\n## Payload round trip (send N bytes -> receiver gets them -> ack), median\n")
    print("| Size | EasySHM write/read | EasySHM zero-copy | TCP localhost | Throughput (copy / zero-copy / TCP) |")
    print("|---|---|---|---|---|")
    for size in PAYLOAD_SIZES:
        iters = max(20, min(args.iters, (256 * 1024 * 1024) // size))
        shm_s = summarize(bench_shm(iters, 10, size))
        zc_s = summarize(bench_shm(iters, 10, size, zero_copy=True))
        tcp_s = summarize(bench_tcp(iters, 10, size))
        mbps = lambda st: size / (st["p50_us"] / 1e6) / 1e6  # noqa: E731
        results["payload"].append({"size": size, "easyshm": shm_s, "easyshm_zero_copy": zc_s, "tcp": tcp_s})
        print(f"| {human(size)} | {shm_s['p50_us']:.1f} µs | {zc_s['p50_us']:.1f} µs | {tcp_s['p50_us']:.1f} µs | "
              f"{mbps(shm_s):,.0f} / {mbps(zc_s):,.0f} / {mbps(tcp_s):,.0f} MB/s |")

    # 3. Idle CPU
    results["idle_cpu_percent"] = idle_cpu_percent()
    print(f"\n## Idle CPU\n\nOne open segment, no traffic: {results['idle_cpu_percent']:.2f} % of one core")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nRaw results written to {args.json}")


if __name__ == "__main__":
    main()
