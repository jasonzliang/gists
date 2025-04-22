import numpy as np
from functools import lru_cache

def det_1x1(m):
    return m[0][0]

def det_2x2(m):
    return m[0][0] * m[1][1] - m[0][1] * m[1][0]

def det(m):
    assert len(m.shape) == 2 and m.shape[0] == m.shape[1]
    if m.shape[0] == 1:
        return det_1x1(m)
    if m.shape[0] == 2:
        return det_2x2(m)
    total = 0.0
    for i in range(m.shape[1]):
        total += (-1)**i * m[0][i] * det(m[1:,np.arange(m.shape[1]) != i])
        # total += (-1)**i * m[0][i] * det(np.delete(m[1:,:], i, axis=1))
    return total

def det_with_memo(matrix):
    """Calculate determinant with memoization for submatrices"""
    # Convert to tuple of tuples for hashability
    matrix_tuple = tuple(tuple(row) for row in matrix)
    return _det_recursive(matrix_tuple)

@lru_cache(maxsize=None)
def _det_recursive(matrix_tuple):
    """Recursive determinant calculation with memoization"""
    # Convert back to numpy array for calculations
    matrix = np.array(matrix_tuple)
    n = len(matrix)

    # Base cases
    if n == 1:
        return matrix[0, 0]
    if n == 2:
        return matrix[0, 0] * matrix[1, 1] - matrix[0, 1] * matrix[1, 0]

    # Recursive case with memoization
    det = 0
    for j in range(n):
        # Create submatrix by removing first row and column j
        submatrix = np.delete(np.delete(matrix, 0, axis=0), j, axis=1)
        # Convert to tuple for memoization
        submatrix_tuple = tuple(tuple(row) for row in submatrix)
        # Add term to determinant
        det += ((-1) ** j) * matrix[0, j] * _det_recursive(submatrix_tuple)

    return det

if __name__ == "__main__":
    x = np.random.rand(3, 3)
    print(np.linalg.det(x))
    print(det(x))
    print(det_with_memo(x))
