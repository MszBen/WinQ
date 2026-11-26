"""Auto-follow / auto-continue: sample-accurate scheduling in the Mixer, the transport
rules (selection skips the chain, Esc/Stop All cancel), and the full window."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from winq.audio.engine import (
    Cancel,
    Mixer,
    Play,
    StartNow,
    Stop,
    StopAll,
    VoiceCancelled,
    VoiceEnded,
    VoiceStarted,
)
from winq.audio.voice import INFINITE, Voice
from winq.control.transport import GoAction, Transport, selection_after
from winq.model.cue import CONTINUE, FOLLOW, INFINITE_REPEATS, AudioCue
from winq.model.show_file import load_show, save_show

SR = 1000


def const(n: int, value: float) -> np.ndarray:
    return np.full((n, 2), value, dtype=np.float32)


def render(m: Mixer, total: int, block: int) -> np.ndarray:
    out = []
    done = 0
    while done < total:
        n = min(block, total - done)
        out.append(m.render(n))
        done += n
    return np.concatenate(out)[:, 0]


# ------------------------------------------------------------------- mixer
@pytest.mark.parametrize("block", [1, 7, 64, 256])
def test_follow_starts_on_the_exact_sample(block):
    m = Mixer(SR)
    m.send(Play(Voice(1, const(30, 0.25), SR)))
    m.send(Play(Voice(2, const(20, 0.5), SR), after=1))
    out = render(m, 60, block)
    np.testing.assert_allclose(out[:30], 0.25)
    np.testing.assert_allclose(out[30:50], 0.5)  # no gap, no overlap
    np.testing.assert_allclose(out[50:], 0.0)


@pytest.mark.parametrize("block", [1, 13, 256])
def test_follow_with_delay_and_three_link_chain(block):
    m = Mixer(SR)
    m.send(Play(Voice(1, const(10, 0.1), SR)))
    m.send(Play(Voice(2, const(10, 0.2), SR), delay_frames=5, after=1))
    m.send(Play(Voice(3, const(10, 0.3), SR), after=2))
    out = render(m, 40, block)
    np.testing.assert_allclose(out[:10], 0.1)
    np.testing.assert_allclose(out[10:15], 0.0)  # the 5-frame delay
    np.testing.assert_allclose(out[15:25], 0.2)
    np.testing.assert_allclose(out[25:35], 0.3)


def test_continue_starts_delay_after_the_first_starts():
    m = Mixer(SR)
    m.send(Play(Voice(1, const(100, 0.1), SR)))
    m.send(Play(Voice(2, const(100, 0.2), SR), delay_frames=30, parent=1))
    out = render(m, 60, 16)
    np.testing.assert_allclose(out[:30], 0.1)
    np.testing.assert_allclose(out[30:60], 0.3, rtol=1e-5)  # both playing


def test_continue_with_zero_delay_starts_together():
    m = Mixer(SR)
    m.send(Play(Voice(1, const(100, 0.1), SR)))
    m.send(Play(Voice(2, const(100, 0.2), SR), parent=1))
    np.testing.assert_allclose(m.render(10)[:, 0], 0.3, rtol=1e-5)


def test_infinite_loop_then_follow_after_loop_ends():
    m = Mixer(SR)
    m.send(Play(Voice(1, const(10, 0.1), SR, repeats=INFINITE)))
    m.send(Play(Voice(2, const(10, 0.5), SR), after=1))
    render(m, 95, 32)
    assert m.pending_voice_count == 1  # still looping, follower waits
    from winq.audio.engine import Devamp

    m.send(Devamp(1))
    out = render(m, 20, 32)  # 5 frames of the last pass, then the follower
    np.testing.assert_allclose(out[:5], 0.1)
    np.testing.assert_allclose(out[5:15], 0.5)


def test_stopping_cancels_followers_and_their_chain():
    m = Mixer(SR)
    m.send(Play(Voice(1, const(100, 0.1), SR)))
    m.send(Play(Voice(2, const(10, 0.5), SR), after=1))
    m.send(Play(Voice(3, const(10, 0.5), SR), after=2))
    m.send(Play(Voice(4, const(10, 0.5), SR), delay_frames=50, parent=1))
    m.render(10)
    m.poll_events()
    m.send(Stop(1, 5))
    m.render(10)
    events = m.poll_events()
    cancelled = {e.voice_id for e in events if isinstance(e, VoiceCancelled)}
    assert cancelled == {2, 3, 4}  # cancelled at Stop time, not after the fade
    assert VoiceEnded(1, natural=False) in events
    np.testing.assert_allclose(render(m, 100, 32), 0.0)


def test_continue_child_survives_parent_ending_naturally():
    m = Mixer(SR)
    m.send(Play(Voice(1, const(10, 0.1), SR)))
    m.send(Play(Voice(2, const(10, 0.5), SR), delay_frames=20, parent=1))
    out = render(m, 40, 8)
    np.testing.assert_allclose(out[20:30], 0.5)


def test_stop_all_cancels_pending():
    m = Mixer(SR)
    m.send(Play(Voice(1, const(100, 0.1), SR)))
    m.send(Play(Voice(2, const(10, 0.5), SR), delay_frames=500))
    m.render(4)
    m.send(StopAll(5))
    render(m, 20, 4)
    assert m.pending_voice_count == 0 and m.active_voice_count == 0
    assert VoiceCancelled(2) in m.poll_events()


def test_start_now_and_cancel():
    m = Mixer(SR)
    m.send(Play(Voice(1, const(1000, 0.1), SR)))
    m.send(Play(Voice(2, const(100, 0.2), SR), after=1))
    m.send(Play(Voice(3, const(100, 0.4), SR), delay_frames=900))
    m.render(10)
    m.send(StartNow(2))
    m.send(Cancel(3))
    m.send(Cancel(1))  # not pending: ignored
    out = m.render(10)[:, 0]
    np.testing.assert_allclose(out, 0.3, rtol=1e-5)
    events = m.poll_events()
    assert VoiceStarted(2) in events and VoiceCancelled(3) in events


def test_follow_of_a_voice_that_already_ended_is_cancelled():
    m = Mixer(SR)
    m.send(Play(Voice(2, const(10, 0.5), SR), after=99))
    m.render(10)
    assert VoiceCancelled(2) in m.poll_events()


# ------------------------------------------------------------------- transport rules
class FakePlayer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._next = 1
        self.unplayable: set[str] = set()

    def start(self, cue, *, delay=0.0, after=None, parent=None):
        if cue.id in self.unplayable:
            return None
        vid = self._next
        self._next += 1
        self.calls.append(("start", cue.name, vid, delay, after, parent))
        return vid

    def start_now(self, vid):
        self.calls.append(("start_now", vid))

    def cancel(self, vid):
        self.calls.append(("cancel", vid))

    def devamp(self, vid):
        self.calls.append(("devamp", vid))

    def stop(self, vid, fade):
        self.calls.append(("stop", vid, fade))

    def stop_all(self, fade):
        self.calls.append(("stop_all", fade))


def cue(name, mode="none", delay=0.0, **kw) -> AudioCue:
    c = AudioCue(
        file_path=Path(f"C:/a/{name}.wav"),
        name=name,
        continue_mode=mode,
        continue_delay=delay,
        **kw,
    )
    c.file_duration = 10.0
    return c


def test_go_schedules_the_whole_chain():
    p = FakePlayer()
    t = Transport(p)
    cues = [cue("a", FOLLOW, 1.0), cue("b", CONTINUE, 2.0), cue("c"), cue("d")]
    r = t.go(cues, 0)
    assert r.action is GoAction.STARTED and r.selection == 3  # skipped past b and c
    assert p.calls == [
        ("start", "a", 1, 0.0, None, None),
        ("start", "b", 2, 1.0, 1, None),  # follows a's end (+1 s)
        ("start", "c", 3, 2.0, None, 2),  # 2 s after b starts
    ]
    assert [x.cue.name for x in t.started] == ["a"]
    assert [x.cue.name for x in t.running] == ["a", "b", "c"]
    assert all(x.pending for x in t.running[1:])


def test_pending_cues_join_esc_order_when_they_start():
    p = FakePlayer()
    t = Transport(p)
    cues = [cue("a", FOLLOW), cue("b"), cue("x")]
    t.go(cues, 0)  # a playing, b pending
    t.go(cues, 2)  # x playing
    assert t.stop_oldest().cue.name == "a"  # pending b is skipped by Esc
    t.voice_started(2)  # b becomes audible after x started
    assert [x.cue.name for x in t.started] == ["a", "x", "b"]


def test_selection_after_chain_rules():
    cues = [cue("a", FOLLOW), cue("b", FOLLOW), cue("c")]
    assert selection_after(cues, 0) == 2  # c is the last cue: stays
    cues = [cue("a", FOLLOW), cue("loop", repeats=INFINITE_REPEATS), cue("z")]
    assert selection_after(cues, 0) == 1  # stops on the looping cue in the chain
    cues = [cue("a"), cue("b")]
    assert selection_after(cues, 0) == 1


def test_go_on_a_pending_cue_starts_it_now():
    p = FakePlayer()
    t = Transport(p)
    cues = [cue("a", FOLLOW, 5.0), cue("b"), cue("c")]
    t.go(cues, 0)
    r = t.go(cues, 1)  # operator jumps ahead
    assert r.action is GoAction.STARTED and r.voice_id == 2
    assert p.calls[-1] == ("start_now", 2)
    assert r.selection == 2


def test_stopping_a_pending_row_cancels_it():
    p = FakePlayer()
    t = Transport(p)
    t.go([cue("a", FOLLOW), cue("b")], 0)
    t.stop_voice(2)
    assert p.calls[-1] == ("cancel", 2)
    t.voice_cancelled(2)
    assert [x.cue.name for x in t.running] == ["a"]


def test_chain_breaks_at_missing_file():
    p = FakePlayer()
    t = Transport(p)
    cues = [cue("a", FOLLOW), cue("gone", FOLLOW), cue("c")]
    p.unplayable.add(cues[1].id)
    r = t.go(cues, 0)
    assert r.chain_broken_at is cues[1]
    assert [x.cue.name for x in t.running] == ["a"]


def test_loop_end_on_infinite_cue_skips_its_chain():
    p = FakePlayer()
    t = Transport(p)
    cues = [cue("loop", FOLLOW, repeats=INFINITE_REPEATS), cue("after"), cue("next")]
    r = t.go(cues, 0)
    assert r.selection == 0  # stays on the loop
    r = t.go(cues, 0)
    assert r.action is GoAction.LOOP_ENDED and r.selection == 2  # past "after"


# ------------------------------------------------------------------- show file
def test_then_settings_roundtrip(tmp_path):
    c = cue("a", FOLLOW, 1.5)
    save_show(tmp_path / "s.winq", [c])
    (loaded,) = load_show(tmp_path / "s.winq").cues
    assert (loaded.continue_mode, loaded.continue_delay) == (FOLLOW, 1.5)


def test_unknown_then_mode_falls_back(tmp_path):
    import json

    path = tmp_path / "s.winq"
    path.write_text(json.dumps({"cues": [{"file": "a.wav", "continue_mode": "teleport"}]}))
    assert load_show(path).cues[0].continue_mode == "none"


# ------------------------------------------------------------------- full window
def write_tone(path: Path, seconds: float, value: float, sr: int = 48000) -> Path:
    sf.write(path, np.full((int(seconds * sr), 1), value, dtype=np.float32), sr)
    return path


def wrender(w, seconds: float) -> np.ndarray:
    n = int(seconds * w.engine.samplerate / 480) + 1
    return np.concatenate([w.engine.mixer.render(480) for _ in range(n)])[:, 0]


@pytest.fixture
def window(make_window, tmp_path):
    w = make_window()
    sr = w.engine.samplerate  # match the device rate, so no resampling ripple in the checks
    w.add_files(
        [
            write_tone(tmp_path / "a.wav", 0.1, 0.1, sr),
            write_tone(tmp_path / "b.wav", 0.1, 0.2, sr),
            write_tone(tmp_path / "c.wav", 0.1, 0.4, sr),
        ]
    )
    w.cue_view.select_row(0)
    return w


def test_window_follow_chain_plays_seamlessly(window):
    w = window
    a = w.cue_model.cue_at(0)
    w.edit_cue(a.id, "continue_mode", FOLLOW, "then")
    assert w.cue_model.data(w.cue_model.index(0, 6)) == "↓ at end"
    w.go()
    assert w.cue_view.selected_row() == 2  # skipped b
    w._tick()
    assert w.cue_model.data(w.cue_model.index(1, 0)) == "⧗"  # b waiting
    out = wrender(w, 0.25)
    n = int(0.1 * w.engine.samplerate)
    np.testing.assert_allclose(out[:n], 0.1, atol=1e-3)  # 16-bit WAV
    np.testing.assert_allclose(out[n : 2 * n], 0.2, atol=1e-3)  # b right after a, no gap
    w._tick()
    assert w.transport.running == ()


def test_window_escape_cancels_the_follow(window):
    w = window
    a = w.cue_model.cue_at(0)
    w.edit_cue(a.id, "continue_mode", FOLLOW, "then")
    w.go()
    wrender(w, 0.02)
    w._tick()
    w.stop_oldest()
    out = wrender(w, 1.5)
    assert np.abs(out[-1000:]).max() == 0.0  # b never started
    w._tick()
    assert w.transport.running == ()


def test_window_panic_cancels_pending(window):
    w = window
    a = w.cue_model.cue_at(0)
    w.edit_cue(a.id, "continue_mode", CONTINUE, "then")
    w.edit_cue(a.id, "continue_delay", 5.0, "then delay")
    w.go()
    wrender(w, 0.02)
    w._tick()
    assert any(r.pending for r in w.transport.running)
    w.panic()
    wrender(w, 1.0)
    w._tick()
    assert w.transport.running == ()


def test_inspector_then_controls(window):
    w = window
    tl = w.inspector.time_loops
    assert not tl.then_delay.isEnabled()
    tl.then.setCurrentIndex(tl.then.findData(CONTINUE))
    assert w.cue_model.cue_at(0).continue_mode == CONTINUE
    assert tl.then_delay.isEnabled()
    tl.then_delay.setValue(2.5)
    assert w.cue_model.cue_at(0).continue_delay == 2.5
    assert w.cue_model.data(w.cue_model.index(0, 6)) == "↓ with +2.5s"
    w.undo_stack.undo()
    w.undo_stack.undo()
    assert w.cue_model.cue_at(0).continue_mode == "none"
