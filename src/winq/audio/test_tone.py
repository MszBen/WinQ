"""Speaker check: a short tone on the left channel, a gap, then on the right."""

from __future__ import annotations

import numpy as np

from winq.audio.dsp import db_to_gain
from winq.audio.voice import Voice

TONE_HZ = 440.0
TONE_DB = -18.0  # dBFS: clearly audible, not startling
TONE_SECONDS = 0.8
GAP_SECONDS = 0.25
EDGE_SECONDS = 0.01  # de-click ramp at each end of each tone


def tone_buffer(samplerate: int) -> np.ndarray:
    n = int(TONE_SECONDS * samplerate)
    gap = int(GAP_SECONDS * samplerate)
    t = np.arange(n, dtype=np.float64) / samplerate
    tone = np.sin(2 * np.pi * TONE_HZ * t) * db_to_gain(TONE_DB)
    edge = max(1, int(EDGE_SECONDS * samplerate))
    ramp = np.linspace(0.0, 1.0, edge)
    tone[:edge] *= ramp
    tone[-edge:] *= ramp[::-1]
    out = np.zeros((2 * n + gap, 2), dtype=np.float32)
    out[:n, 0] = tone  # left
    out[n + gap :, 1] = tone  # right
    return out


def tone_voice(voice_id: int, samplerate: int) -> Voice:
    return Voice(voice_id, tone_buffer(samplerate), samplerate, tag="test-tone")
