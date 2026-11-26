"""Loop fades: the dip (or crossfade) where a repeating cue jumps back to Loop start."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from winq.audio.voice import INFINITE, Voice
from winq.model.cue import (
    LOOP_FADE_CROSSFADE,
    LOOP_FADE_JUMP,
    LOOP_FADE_LOOP_END,
    LOOP_FADE_PASS,
    AudioCue,
)
from winq.model.show_file import cue_from_dict, cue_to_dict

SR = 1000
N = 1000  # 1 s buffer


def flat() -> np.ndarray:
    return np.ones((N, 2), np.float32)


def ramp() -> np.ndarray:
    x = (np.arange(N, dtype=np.float32) / N)[:, None]
    return np.repeat(x, 2, axis=1)


def voice(buf=None, **kw) -> Voice:
    kw = {"loop_start_frame": 200, "loop_end_frame": 600, **kw}
    return Voice(1, flat() if buf is None else buf, SR, **kw)


def play(v: Voice, limit: int = 20 * N) -> np.ndarray:
    out = v.render(limit)
    assert v.finished
    return out[: v.last_produced, 0]


# ------------------------------------------------------------------ dips
def test_jump_dips_before_every_jump_and_fades_in_after_it():
    v = voice(repeats=1, loop_fade_in_frames=50, loop_fade_out_frames=50)
    out = play(v)
    assert len(out) == 1000 + 400  # whole first play, one section pass, stop
    assert out[900] == pytest.approx(1.0)
    assert out[999] == pytest.approx(0.0, abs=1e-6)  # fade-out ends on silence at End
    assert out[1000] == pytest.approx(0.0)  # fade-in starts from silence at Loop start
    assert out[1025] == pytest.approx(0.5, abs=0.05)
    assert out[1399] == pytest.approx(1.0)  # last pass: no jump, no dip


def test_loop_end_mode_skips_the_dip_before_end_after_a_whole_first_play():
    v = voice(repeats=2, loop_fade_in_frames=50, loop_fade_out_frames=50,
              loop_fade_mode=LOOP_FADE_LOOP_END)  # fmt: skip
    out = play(v)
    assert out[990] == pytest.approx(1.0)  # no fade before End
    assert out[1000] == pytest.approx(0.0)  # but the jump still fades in
    assert out[1399] == pytest.approx(0.0, abs=1e-6)  # pass 1 ends at Loop end: dip
    assert out[1799] == pytest.approx(1.0)  # last pass


def test_pass_mode_also_dips_into_the_outro():
    kw = {"repeats": 1, "outro": True, "loop_fade_in_frames": 50, "loop_fade_out_frames": 50}
    pas = play(voice(loop_fade_mode=LOOP_FADE_PASS, **kw))
    jmp = play(voice(**kw))
    assert len(pas) == len(jmp) == 1000 + 400 + 400
    assert pas[1399] == pytest.approx(0.0, abs=1e-6) and pas[1400] == pytest.approx(0.0)
    assert pas[1460] == pytest.approx(1.0)
    assert jmp[1399] == pytest.approx(1.0) and jmp[1400] == pytest.approx(1.0)


def test_without_repeats_loop_fades_do_nothing():
    out = play(voice(repeats=0, loop_fade_out_frames=50, loop_fade_mode=LOOP_FADE_PASS))
    assert np.allclose(out, 1.0)


def test_whole_track_repeats_dip_at_the_track_ends():
    v = Voice(1, flat(), SR, repeats=1, loop_fade_in_frames=50, loop_fade_out_frames=50)
    out = play(v)
    assert len(out) == 2000
    assert out[999] == pytest.approx(0.0, abs=1e-6) and out[1000] == pytest.approx(0.0)
    assert out[1999] == pytest.approx(1.0)


# ------------------------------------------------------------------ crossfade
def test_crossfade_joins_the_jump_seamlessly_and_keeps_the_timing():
    buf = ramp()
    v = voice(buf, first_full=False, repeats=1, loop_fade_out_frames=100,
              loop_fade_mode=LOOP_FADE_CROSSFADE)  # fmt: skip
    out = play(v)
    assert len(out) == 600 + 400
    assert np.allclose(out[:500], buf[:500, 0])  # untouched before the crossfade
    # Across the jump the signal carries on from the audio just before Loop start.
    assert out[599] == pytest.approx(buf[199, 0], abs=1e-6)
    assert out[600] == pytest.approx(buf[200, 0])
    assert np.max(np.abs(np.diff(out))) < 0.01  # no step anywhere
    assert np.allclose(out[1000 - 50 :], buf[550:600, 0])  # last pass: no crossfade


def test_crossfade_is_limited_by_the_audio_before_loop_start():
    v = voice(ramp(), loop_start_frame=20, first_full=False, repeats=1,
              loop_fade_out_frames=100, loop_fade_mode=LOOP_FADE_CROSSFADE)  # fmt: skip
    out = play(v)
    assert out[579] == pytest.approx(ramp()[579, 0])  # before the 20-frame crossfade
    assert out[599] == pytest.approx(ramp()[19, 0], abs=1e-6)
    # Loop start at the very top of the file: nothing to blend, a plain jump.
    plain = play(Voice(1, ramp(), SR, repeats=1, loop_fade_out_frames=100,
                       loop_fade_mode=LOOP_FADE_CROSSFADE))  # fmt: skip
    assert np.allclose(plain, np.tile(ramp()[:, 0], 2))


def test_crossfade_has_no_dip_on_flat_audio():
    v = voice(first_full=False, repeats=3, loop_fade_in_frames=80, loop_fade_out_frames=100,
              loop_fade_mode=LOOP_FADE_CROSSFADE)  # fmt: skip
    assert np.allclose(play(v), 1.0, atol=1e-6)


# ------------------------------------------------------------------ devamp
def test_devamp_before_the_dip_plays_on_at_full_level():
    v = voice(first_full=False, repeats=INFINITE, outro=True, loop_fade_in_frames=50,
              loop_fade_out_frames=50)  # fmt: skip
    a = v.render(300)  # inside the intro, before its dip at 550
    v.devamp()
    out = np.concatenate([a[:, 0], play(v)])
    assert len(out) == 1000  # intro straight into the outro
    assert np.allclose(out, 1.0)


def test_devamp_inside_the_dip_lets_it_finish_and_fades_the_outro_in():
    v = voice(first_full=False, repeats=INFINITE, outro=True, loop_fade_in_frames=50,
              loop_fade_out_frames=50)  # fmt: skip
    a = v.render(575)  # halfway down the dip before Loop end (600)
    v.devamp()
    out = np.concatenate([a[:, 0], play(v)])
    assert len(out) == 1000
    assert out[599] == pytest.approx(0.0, abs=1e-6)
    assert out[600] == pytest.approx(0.0) and out[700] == pytest.approx(1.0)


def test_devamp_inside_a_crossfade_plays_one_more_pass():
    v = voice(ramp(), first_full=False, repeats=INFINITE, loop_fade_out_frames=100,
              loop_fade_mode=LOOP_FADE_CROSSFADE)  # fmt: skip
    a = v.render(550)  # inside the crossfade (500..600)
    v.devamp()
    assert not v.is_looping
    assert v.remaining_frames() == 50 + 400
    out = np.concatenate([a[:, 0], play(v)])
    assert len(out) == 600 + 400
    assert np.max(np.abs(np.diff(out))) < 0.01


# ------------------------------------------------------------------ model
def test_show_file_round_trip_and_unknown_mode(tmp_path):
    c = AudioCue(file_path=Path("x.wav"), loop_fade_in=0.25, loop_fade_out=0.5,
                 loop_fade_mode=LOOP_FADE_PASS)  # fmt: skip
    back = cue_from_dict(cue_to_dict(c, tmp_path), tmp_path)
    assert (back.loop_fade_in, back.loop_fade_out, back.loop_fade_mode) == (
        0.25, 0.5, LOOP_FADE_PASS)  # fmt: skip
    old = cue_from_dict({"file": "x.wav", "loop_fade_mode": "??"}, tmp_path)
    assert (old.loop_fade_in, old.loop_fade_out, old.loop_fade_mode) == (0, 0, LOOP_FADE_JUMP)


def test_has_loop_fades():
    c = AudioCue(file_path=Path("x.wav"))
    assert not c.has_loop_fades
    c.loop_fade_in = 0.1
    assert c.has_loop_fades
    c.loop_fade_mode = LOOP_FADE_CROSSFADE  # the crossfade only uses the fade-out length
    assert not c.has_loop_fades


# ------------------------------------------------------------------ in the window
@pytest.fixture
def window(make_window, tmp_path):
    import soundfile as sf

    path = tmp_path / "bed.wav"
    sf.write(path, np.full((48000 * 4, 2), 0.1, np.float32), 48000)  # 4 s
    w = make_window()
    w.add_files([path])
    w.cue_view.select_row(0)
    w.cache.get(w.cue_model.cue_at(0).file_path)
    w._load_inspector_audio()
    return w


def section(w, **extra) -> AudioCue:
    c = w.cue_model.cue_at(0)
    setup = {"loop_section": True, "loop_start": 1.0, "loop_end": 2.0, "repeats": 2, **extra}
    w.edit_cue_group(c.id, list(setup.items()), "setup")
    return c


def test_fields_need_a_repeating_cue_with_a_section(window):
    w = window
    tl = w.inspector.time_loops
    c = w.cue_model.cue_at(0)
    assert not tl.loop_fade_in.isEnabled()
    w.edit_cue(c.id, "repeats", 2, "repeats")
    assert not tl.loop_fade_in.isEnabled()  # no section, preference off
    w.edit_cue(c.id, "loop_section", True, "section")
    assert tl.loop_fade_in.isEnabled() and tl.loop_fade_out.isEnabled()
    w.edit_cue(c.id, "repeats", 0, "repeats")
    assert not tl.loop_fade_mode.isEnabled()


def test_preference_allows_loop_fades_without_a_section(window):
    w = window
    tl = w.inspector.time_loops
    c = w.cue_model.cue_at(0)
    w.edit_cue(c.id, "repeats", 1, "repeats")
    prefs = w.prefs
    prefs.loop_fades_whole = True
    w.apply_prefs(prefs)
    assert tl.loop_fade_in.isEnabled() and tl.waveform.loop_fades_on
    assert tl.waveform.loop_region == (0.0, 4.0)
    tl.loop_fade_out.setValue(0.5)
    v = w.player._make_voice(c)
    assert v.loop_fade_out_frames == v.samplerate // 2


def test_typed_fades_are_clamped_and_undoable(window):
    w = window
    tl = w.inspector.time_loops
    c = section(w)
    tl.loop_fade_in.setValue(5.0)  # longer than the 1 s section
    assert c.loop_fade_in == 1.0
    tl.loop_fade_out.setValue(0.25)
    assert c.loop_fade_out == 0.25
    tl.loop_fade_mode.setCurrentIndex(tl.loop_fade_mode.findData(LOOP_FADE_CROSSFADE))
    assert c.loop_fade_mode == LOOP_FADE_CROSSFADE
    assert not tl.loop_fade_in.isEnabled() and tl.loop_fade_out_label.text() == "Length"
    tl.loop_fade_out.setValue(3.0)  # only 1 s of audio before Loop start, 1 s section
    assert c.loop_fade_out == 1.0
    w.undo_stack.undo()
    w.undo_stack.undo()
    assert (c.loop_fade_mode, c.loop_fade_out) == (LOOP_FADE_JUMP, 0.25)


def test_dragging_a_loop_fade_handle(window, qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    w = window
    w.inspector.setCurrentIndex(1)
    w.resize(1280, 720)
    w.show()
    qapp.processEvents()
    c = section(w)
    wave = w.inspector.time_loops.waveform
    y = int(wave._wave_rect().height() - 4)  # the bottom handle row
    x0 = int(wave.x_of(2.0))  # fade-out handle sits on Loop end while it's 0
    x1 = int(wave.x_of(1.5))
    no = Qt.KeyboardModifier.NoModifier
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, no, QPoint(x0, y))
    QTest.mouseMove(wave, QPoint(x1, y))
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, no, QPoint(x1, y))
    assert c.loop_fade_out == pytest.approx(0.5, abs=0.02)
    assert c.loop_end == 2.0  # the loop line itself didn't move
    w.undo_stack.undo()
    assert c.loop_fade_out == 0.0
