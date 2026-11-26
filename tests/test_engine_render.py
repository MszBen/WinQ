"""Mixer tests (no audio device): commands, mixing, events, master gain, clipping."""

from __future__ import annotations

import numpy as np

from winq.audio.engine import (
    Devamp,
    Mixer,
    Play,
    SetGain,
    SetMaster,
    Stop,
    StopAll,
    VoiceEnded,
    VoiceStarted,
)
from winq.audio.voice import INFINITE, Voice

SR = 1000


def const_buffer(n: int, value: float) -> np.ndarray:
    return np.full((n, 2), value, dtype=np.float32)


def test_voices_are_summed_and_overlap():
    m = Mixer(SR)
    m.send(Play(Voice(1, const_buffer(100, 0.25), SR)))
    m.send(Play(Voice(2, const_buffer(100, 0.25), SR)))
    out = m.render(10)
    np.testing.assert_allclose(out, 0.5)
    assert m.active_voice_count == 2


def test_started_and_ended_events():
    m = Mixer(SR)
    m.send(Play(Voice(7, const_buffer(10, 0.1), SR)))
    m.render(5)
    assert m.poll_events() == [VoiceStarted(7)]
    m.render(10)
    assert m.poll_events() == [VoiceEnded(7)]
    assert m.active_voice_count == 0
    assert m.poll_events() == []


def test_commands_route_to_the_right_voice():
    m = Mixer(SR)
    m.send(Play(Voice(1, const_buffer(1000, 0.1), SR, repeats=INFINITE)))
    m.send(Play(Voice(2, const_buffer(1000, 0.1), SR, repeats=INFINITE)))
    m.render(1)
    m.send(Stop(1, 10))
    m.render(20)
    m.poll_events()
    m.render(1)
    assert m.active_voice_count == 1
    m.send(SetGain(2, 0.0))
    m.render(20)
    np.testing.assert_allclose(m.render(5), 0.0)
    m.send(Devamp(2))
    m.render(2000)
    assert m.active_voice_count == 0
    assert VoiceEnded(2) in m.poll_events()


def test_commands_for_unknown_voices_are_ignored():
    m = Mixer(SR)
    m.send(Stop(99, 10))
    m.send(SetGain(99, 0.5))
    m.send(Devamp(99))
    m.render(10)  # must not raise


def test_stop_all():
    m = Mixer(SR)
    for i in range(3):
        m.send(Play(Voice(i, const_buffer(1000, 0.1), SR, repeats=INFINITE)))
    m.render(1)
    m.send(StopAll(20))
    m.render(30)
    assert m.active_voice_count == 0


def test_master_gain_and_clipping():
    m = Mixer(SR)
    m.send(Play(Voice(1, const_buffer(1000, 0.8), SR)))
    m.send(Play(Voice(2, const_buffer(1000, 0.8), SR)))
    out = m.render(5)
    np.testing.assert_allclose(out, 1.0)  # 1.6 clipped
    assert m.peak == 1.0
    m.send(SetMaster(0.5))
    m.render(20)  # past the ramp
    np.testing.assert_allclose(m.render(5), 0.8)
