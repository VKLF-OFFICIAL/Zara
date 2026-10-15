import numpy as np
from zara.audio.identity import cosine, decide


def test_cosine_identical_is_one():
    v = np.array([1.0, 2.0, 3.0])
    assert abs(cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_is_zero():
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    assert abs(cosine(a, b)) < 1e-6


def test_decide_threshold():
    assert decide(0.80, threshold=0.75) is True
    assert decide(0.74, threshold=0.75) is False
