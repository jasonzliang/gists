import numpy as np
import time
import os

def measure_memory_bandwidth(size_mb=512, iterations=50):
    """
    Measure memory bandwidth using STREAM-like tests.
    Optimized for M1 MacBook Pro with 16GB RAM.
    """
    # Create arrays
    n = size_mb * 1024 * 1024 // 8  # Number of double-precision elements

    # Aligned memory allocation
    a = np.empty(n, dtype=np.float64)
    b = np.empty(n, dtype=np.float64)
    c = np.empty(n, dtype=np.float64)

    # Initialize arrays
    a[:] = 1.0
    b[:] = 2.0
    c[:] = 0.0

    # Warmup
    np.copyto(c, a)

    results = {}

    # Test 1: Copy (c = a)
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        np.copyto(c, a)
        times.append(time.perf_counter() - start)
    copy_bw = (n * 8 * 2) / min(times) / 1e9
    results["Copy"] = copy_bw

    # Test 2: Scale (b = scalar * c)
    scalar = 3.0
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        np.multiply(c, scalar, out=b)
        times.append(time.perf_counter() - start)
    scale_bw = (n * 8 * 2) / min(times) / 1e9
    results["Scale"] = scale_bw

    # Test 3: Add (c = a + b)
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        np.add(a, b, out=c)
        times.append(time.perf_counter() - start)
    add_bw = (n * 8 * 3) / min(times) / 1e9
    results["Add"] = add_bw

    # Test 4: Triad (a = b + scalar * c)
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        np.add(b, np.multiply(scalar, c, out=a), out=a)
        times.append(time.perf_counter() - start)
    triad_bw = (n * 8 * 3) / min(times) / 1e9
    results["Triad"] = triad_bw

    return results

def run_optimized_benchmark():
    """Run optimized benchmark"""
    import sys

    print("Memory Bandwidth Benchmark (M1 Optimized)")
    print("=" * 50)
    print(f"Python version: {sys.version.split()[0]}")
    print(f"NumPy version: {np.__version__}")
    print(f"Number of CPUs: {os.cpu_count()}")
    print("=" * 50)

    sizes = [128, 256, 512] # MB

    for size in sizes:
        print(f"\nTesting with array size: {size} MB")
        print("-" * 40)

        results = measure_memory_bandwidth(size_mb=size)

        for test, bandwidth in results.items():
            print(f"{test:8s}: {bandwidth:8.2f} GB/s")

        avg_bandwidth = sum(results.values()) / len(results)
        print(f"{'Average':8s}: {avg_bandwidth:8.2f} GB/s")

if __name__ == "__main__":
    run_optimized_benchmark()
