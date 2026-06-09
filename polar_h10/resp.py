"""
polar_h10.resp - estimate respiration (breaths/min) from chest-accelerometer data.

The Polar H10 has NO respiration output. But the strap sits on the chest, so
breathing shows up as a slow (~0.1-0.6 Hz, i.e. ~6-36 br/min) oscillation on the
accelerometer as the chest expands and the strap tilts. This estimator isolates
that band and counts breaths.

It is an ESTIMATE for a still/seated subject: gross body motion (walking,
talking, gesturing) lives in the same low band and will swamp the breathing
signal. It is not a respiration belt and not a medical measurement.

Transport-independent and float-only, so it runs and is tested under CPython and
runs unchanged on MicroPython.

Pipeline (per axis): a slow EMA tracks gravity/DC and is subtracted (high-pass);
a faster EMA smooths what's left (low-pass) -> a band-passed signal. The axis
with the largest band-pass variance is taken as the breathing axis; rising
zero-crossings of it, gated by a fraction of its amplitude (hysteresis), mark
breaths. The breath period is converted to breaths/min and EMA-smoothed.
"""

import math


def _alpha(fc, fs):
    """EMA smoothing factor for a one-pole filter at cutoff fc (Hz), rate fs (Hz)."""
    return 1.0 - math.exp(-2.0 * math.pi * fc / fs)


class RespirationEstimator:
    def __init__(self, fs=50, f_hp=0.05, f_lp=0.6, rate_smooth=0.3,
                 min_bpm=5, max_bpm=45, thr_frac=0.3):
        self.fs = fs
        self._a_slow = _alpha(f_hp, fs)   # high-pass (gravity/drift removal)
        self._a_fast = _alpha(f_lp, fs)   # low-pass (smoothing)
        self._a_var = _alpha(0.05, fs)    # slow variance tracker per axis
        self._rate_smooth = rate_smooth
        self._thr_frac = thr_frac
        self._min_period = 60.0 / max_bpm
        self._max_period = 60.0 / min_bpm

        self._dc = [None, None, None]
        self._lp = [0.0, 0.0, 0.0]
        self._var = [0.0, 0.0, 0.0]
        self._n = 0
        self.axis = 2                     # current breathing axis (x=0,y=1,z=2)

        self._prev = 0.0
        self._armed = False               # dipped below -thr, ready to count up-cross
        self._t = 0.0                     # elapsed seconds
        self._last_cross = None

        self.rate_bpm = None              # current estimate, breaths/min (or None)
        self.wave = 0.0                   # latest band-passed value (for plotting)
        self.amp = 0.0                    # breathing amplitude (RMS-ish), mG

    def feed(self, x, y, z):
        """Feed one (x, y, z) accelerometer sample (mG). Returns current rate_bpm."""
        self._t += 1.0 / self.fs
        s = (x, y, z)
        for i in range(3):
            if self._dc[i] is None:
                self._dc[i] = s[i]
            self._dc[i] += self._a_slow * (s[i] - self._dc[i])
            hp = s[i] - self._dc[i]
            self._lp[i] += self._a_fast * (hp - self._lp[i])
            bp = self._lp[i]
            self._var[i] += self._a_var * (bp * bp - self._var[i])
        self._n += 1

        # Re-pick the breathing axis periodically (the one with most variance).
        if self._n % 25 == 0:
            best = 0
            for i in (1, 2):
                if self._var[i] > self._var[best]:
                    best = i
            self.axis = best

        v = self._lp[self.axis]
        self.wave = v
        var = self._var[self.axis]
        self.amp = math.sqrt(var) if var > 0 else 0.0
        thr = self._thr_frac * self.amp
        if thr <= 0:
            self._prev = v
            return self.rate_bpm

        if v < -thr:
            self._armed = True
        # Rising zero-crossing, only counted once we've seen a trough since last one.
        if self._armed and self._prev <= 0.0 < v:
            if self._last_cross is not None:
                period = self._t - self._last_cross
                if self._min_period <= period <= self._max_period:
                    bpm = 60.0 / period
                    if self.rate_bpm is None:
                        self.rate_bpm = bpm
                    else:
                        self.rate_bpm += self._rate_smooth * (bpm - self.rate_bpm)
            self._last_cross = self._t
            self._armed = False

        self._prev = v
        return self.rate_bpm
