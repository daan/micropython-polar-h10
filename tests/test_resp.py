"""
Tests for the respiration estimator, driven by a synthetic breathing signal.

We fabricate accelerometer data: gravity on z, a breathing oscillation on x at a
known rate, a heart-rate-band wobble, and a little noise - then check the
estimator recovers the breathing rate within tolerance. Runs under CPython and
(plain asserts) under the MicroPython unix port.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polar_h10.resp import RespirationEstimator


def _synth_rate(true_bpm, fs=50, seconds=90):
    """Feed a synthetic signal at true_bpm and return the final estimate."""
    est = RespirationEstimator(fs=fs)
    f_breath = true_bpm / 60.0
    f_hr = 1.2  # ~72 bpm cardiac wobble, should be filtered out
    out = None
    n = int(fs * seconds)
    # Deterministic pseudo-noise (no Math.random needed; stable across runs).
    for k in range(n):
        t = k / fs
        breath = 40.0 * math.sin(2.0 * math.pi * f_breath * t)
        hr = 8.0 * math.sin(2.0 * math.pi * f_hr * t)
        noise = 3.0 * math.sin(2.0 * math.pi * 7.3 * t + 1.0)
        x = breath + hr + noise
        y = 5.0 * math.sin(2.0 * math.pi * f_breath * t + 0.5)
        z = 1000.0 + hr
        out = est.feed(x, y, z)
    return out, est


def test_recovers_12_bpm():
    rate, est = _synth_rate(12.0)
    assert rate is not None, "no rate produced"
    assert 10.0 <= rate <= 14.0, rate
    assert est.axis == 0, ("expected breathing axis x", est.axis)


def test_recovers_20_bpm():
    rate, _ = _synth_rate(20.0)
    assert rate is not None
    assert 17.0 <= rate <= 23.0, rate


def test_no_rate_before_warmup():
    est = RespirationEstimator(fs=50)
    for _ in range(10):
        r = est.feed(0.0, 0.0, 1000.0)
    assert r is None  # not enough breaths seen yet


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all resp tests passed")
