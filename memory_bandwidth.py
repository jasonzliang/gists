import numpy as np
import time

def measure_memory_bandwidth(size_mb=2000, iterations=3):
    # Create arrays
    n = size_mb * 1024 * 1024 // 8  # Number of double-precision elements
    a = np.ones(n, dtype=np.float64)
    b = np.ones(n, dtype=np.float64)
    c = np.zeros(n, dtype=np.float64)

    # Ensure arrays are in main memory
    a.fill(1.0)
    b.fill(2.0)
    c.fill(0.0)

    # Test 1: Copy bandwidth (c = a)
    start = time.time()
    for _ in range(iterations):
        np.copyto(c, a)
    end = time.time()
    copy_bw = (n * 8 * 2 * iterations) / (end - start) / 1e9  # GB/s

    # Test 2: Add bandwidth (c = a + b)
    start = time.time()
    for _ in range(iterations):
        np.add(a, b, out=c)
    end = time.time()
    add_bw = (n * 8 * 3 * iterations) / (end - start) / 1e9  # GB/s

    return {"Copy": copy_bw, "Add": add_bw}

if __name__ == "__main__":
    print("Running memory bandwidth test...")
    print("This will take a few seconds...")
    results = measure_memory_bandwidth()
    print(f"Copy bandwidth: {results['Copy']:.2f} GB/s")
    print(f"Add bandwidth: {results['Add']:.2f} GB/s")
    print(f"Average bandwidth: {(results['Copy'] + results['Add'])/2:.2f} GB/s")
