"""Small DSP helpers shared by the engine: level conversion, fade curves, smoothed gain."""

from __future__ import annotations

import math

import numpy as np

SILENCE_DB = -120.0  # at or below this, a level is treated as -inf (gain 0)
GAIN_RAMP_SECONDS = 0.010  # every gain change is smoothed over this long (no zipper noise)
MIN_STOP_SECONDS = 0.005  # even an "instant" stop gets this de-click ramp


def db_to_gain(db: float) -> float:
    if db <= SILENCE_DB:  # also catches -inf
        return 0.0
    return 10.0 ** (db / 20.0)


def gain_to_db(gain: float) -> float:
    if gain <= 0.0:
        return -math.inf
    return 20.0 * math.log10(gain)


def fade_in_curve(t: np.ndarray) -> np.ndarray:
    """S-curve (raised cosine) rising 0 → 1 as t goes 0 → 1. `t` must already be in [0, 1]."""
    return (0.5 - 0.5 * np.cos(np.pi * t)).astype(np.float32, copy=False)


def fade_out_curve(t: np.ndarray) -> np.ndarray:
    """S-curve falling 1 → 0 as t goes 0 → 1."""
    return (0.5 + 0.5 * np.cos(np.pi * t)).astype(np.float32, copy=False)


def seconds_to_frames(seconds: float, samplerate: int) -> int:
    return max(0, round(seconds * samplerate))


class GainRamp:
    """A gain value that glides linearly to each new target over a fixed number of frames."""

    def __init__(self, initial: float, ramp_frames: int) -> None:
        self.current = float(initial)
        self.target = float(initial)
        self._ramp_frames = max(1, int(ramp_frames))
        self._step = 0.0
        self._remaining = 0

    def set_target(self, target: float) -> None:
        self.target = float(target)
        self._step = (self.target - self.current) / self._ramp_frames
        self._remaining = self._ramp_frames

    def next_block(self, frames: int) -> float | np.ndarray:
        """Gain for the next `frames` frames: a float when steady, an array while ramping."""
        if self._remaining == 0:
            return self.current
        m = min(frames, self._remaining)
        gains = np.full(frames, self.target, dtype=np.float32)
        gains[:m] = self.current + self._step * np.arange(1, m + 1, dtype=np.float32)
        self._remaining -= m
        if self._remaining == 0:
            gains[m - 1] = self.target  # land exactly on target despite float rounding
        self.current = self.target if self._remaining == 0 else float(gains[m - 1])
        return gains
