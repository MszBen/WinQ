"""Live edits: Time & Loops changes reach copies that are already playing."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from winq.audio.voice import FIRST, INFINITE, LOOP, OUTRO, Voice
from winq.control.transport import Transport

SR = 1000
N = 1000


def ramp() -> np.ndarray:
    x = (np.arange(N, dtype=np.float32) / N)[:, None]
    return np.repeat(x, 2, axis=1)


def flat() -> np.ndarray:
    return np.ones((N, 2), np.float32)


def rest(v: Voice) -> np.ndarray:
    out = v.render(20 * N)
    assert v.finished
    return out[: v.last_produced, 0]


# ------------------------------------------------------------------ Voice.reshape
def test_moving_loop_points_changes_the_next_jump():
    v = Voice(1, ramp(), SR, repeats=INFINITE, loop_start_frame=200, loop_end_frame=600,
              first_full=False)  # fmt: skip
    v.render(300)
    v.reshape(v.reshaped(loop_start_frame=100, loop_end_frame=500))
    v.render(200)  # reaches the new Loop end (500) and jumps to the new Loop start
    assert v.phase == LOOP and v.position == 100  # 300→500, then back to 100
    assert v.seg_start == 100 and v.seg_end == 500


def test_loop_end_moved_before_the_playhead_jumps_back_now_with_a_declick():
    v = Voice(1, ramp(), SR, repeats=INFINITE, loop_start_frame=200, loop_end_frame=600,
              first_full=False)  # fmt: skip
    v.render(400)  # playhead at 400
    v.reshape(v.reshaped(loop_end_frame=300))
    assert v.phase == LOOP and v.position == 200 and v.pass_index == 1
    out = v.render(20)[:, 0]
    n = v._min_stop_frames
    # First frame: almost all old audio (from 400), last: all new audio (from 200).
    assert out[0] == pytest.approx(0.4, abs=0.02)
    assert out[n + 1] == pytest.approx((200 + n + 1) / N, abs=1e-6)
    assert np.max(np.abs(np.diff(out))) < 0.07  # a 5 ms glide, not a step


def test_loop_end_moved_before_the_playhead_on_the_last_pass_ends_naturally():
    v = Voice(1, flat(), SR, repeats=0)
    v.render(800)
    v.reshape(v.reshaped(end_frame=500, loop_end_frame=500))
    out = rest(v)
    assert len(out) == v._min_stop_frames  # just the de-click fade
    assert out[0] < 1.0 and out[-1] == pytest.approx(0.0, abs=1e-6)
    assert v.ended_naturally  # so an auto-follow still fires


def test_switching_to_play_on_mid_loop_adds_the_outro():
    v = Voice(1, flat(), SR, repeats=1, loop_start_frame=200, loop_end_frame=600)
    v.render(1100)  # whole first play, then 100 frames into the one loop pass
    v.reshape(v.reshaped(outro=True))
    assert v.remaining_frames() == 300 + 400
    rest(v)
    assert v.phase == OUTRO and v.ended_naturally


def test_switching_off_play_on_during_the_outro_ends_the_cue():
    v = Voice(1, flat(), SR, repeats=1, loop_start_frame=200, loop_end_frame=600,
              outro=True)  # fmt: skip
    v.render(1500)  # in the outro (600..1000)
    assert v.phase == OUTRO
    v.reshape(v.reshaped(outro=False))
    assert len(rest(v)) == v._min_stop_frames and v.ended_naturally


def test_repeats_raised_and_lowered_while_playing():
    v = Voice(1, flat(), SR, repeats=1)
    v.render(1500)  # second (last) pass
    v.reshape(v.reshaped(repeats=3))
    assert v.remaining_frames() == 500 + 2 * N
    v.reshape(v.reshaped(repeats=0))  # already past it: this pass is the last
    assert v.remaining_frames() == 500 and v.is_final_pass


def test_start_moved_past_the_playhead_jumps_forward():
    v = Voice(1, ramp(), SR)
    v.render(100)
    v.reshape(v.reshaped(start_frame=300, loop_start_frame=300))
    assert v.phase == FIRST and v.position == 300


def test_end_fade_edit_applies_to_the_playing_copy():
    v = Voice(1, flat(), SR)
    v.render(500)
    v.reshape(v.reshaped(end_fade_frames=200))
    out = rest(v)
    assert out[200] == pytest.approx(1.0)  # 700: fade starts at 800
    assert out[-1] == pytest.approx(0.0, abs=1e-6)


def test_a_waiting_copy_takes_the_new_start():
    v = Voice(1, ramp(), SR)
    v.reshape(v.reshaped(start_frame=250, loop_start_frame=250))
    assert v.position == 250
    v.reshape(v.reshaped(start_frame=50, loop_start_frame=50))
    assert v.position == 50


def test_invalid_shape_is_ignored():
    v = Voice(1, ramp(), SR)
    v.render(10)
    v.reshape(v.reshaped(end_frame=5 * N))
    assert v.end_frame == N


# ------------------------------------------------------------------ transport
class FakePlayer:
    def __init__(self):
        self.ids = iter(range(1, 100))
        self.devamped = []

    def start(self, cue, **_kw):
        return next(self.ids)

    def devamp(self, vid):
        self.devamped.append(vid)

    def stop(self, *_a): ...
    def stop_all(self, *_a): ...
    def start_now(self, *_a): ...
    def cancel(self, *_a): ...


def test_making_a_playing_cue_loop_forever_lets_go_end_it():
    from pathlib import Path

    from winq.model.cue import INFINITE_REPEATS, AudioCue

    cue = AudioCue(file_path=Path("x.wav"))
    cue.file_duration = 1.0
    t = Transport(FakePlayer())
    t.go([cue], 0)
    assert t.looping_instance(cue.id) is None
    cue.repeats = INFINITE_REPEATS
    t.repeats_changed(cue.id)
    r = t.looping_instance(cue.id)
    assert r is not None
    t.go([cue], 0)  # ends the loop, starts nothing
    assert len(t.running) == 1 and r.loop_ended
    cue.repeats = 2
    t.repeats_changed(cue.id)
    cue.repeats = INFINITE_REPEATS
    t.repeats_changed(cue.id)
    assert t.looping_instance(cue.id) is None  # its loop was already ended


# ------------------------------------------------------------------ in the window
@pytest.fixture
def window(make_window, tmp_path):
    path = tmp_path / "bed.wav"
    sf.write(path, np.full((48000 * 4, 2), 0.1, np.float32), 48000)  # 4 s
    w = make_window()
    w.add_files([path])
    w.cue_view.select_row(0)
    w.cache.get(w.cue_model.cue_at(0).file_path)
    w._load_inspector_audio()
    return w


def test_inspector_edits_reach_playing_copies_and_preview(window):
    w = window
    c = w.cue_model.cue_at(0)
    w.go()
    w.go()  # two overlapping copies
    w.engine.mixer.render(256)
    w._tick()
    w.start_preview(c.id, 0.0)
    w.engine.mixer.render(256)
    voices = [w.player._voices[r.voice_id] for r in w.transport.instances_of(c.id)]
    voices.append(w.player._voices[w._preview_voice])
    tl = w.inspector.time_loops
    tl.end.setValue(2.0)
    tl.loop_section.setChecked(True)
    w.engine.mixer.render(1)  # the engine applies the commands
    sr = w.engine.samplerate
    for v in voices:
        assert v.end_frame == 2 * sr and v.loop_start_frame > 0
    w.edit_cue(c.id, "repeats", -1, "repeats")
    w.engine.mixer.render(1)
    assert all(v.is_infinite for v in voices)
    assert w.transport.looping_instance(c.id) is not None
