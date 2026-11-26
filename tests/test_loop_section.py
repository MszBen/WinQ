"""Repeat section: model, show file, cue → voice, and the Time & Loops editor."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from winq.model.cue import AFTER_OUTRO, AFTER_STOP, FIRST_FULL, FIRST_INTRO, AudioCue
from winq.model.show_file import cue_from_dict, cue_to_dict
from winq.ui.cue_table import Col


def cue(**kw) -> AudioCue:
    c = AudioCue(file_path=Path("x.wav"), **kw)
    c.file_duration = 10.0
    return c


def test_section_bounds_are_clamped_inside_the_trim():
    assert cue().section_bounds() is None  # off
    assert cue(loop_section=True, loop_start=2, loop_end=5).section_bounds() == (2, 5)
    on = {"loop_section": True, "start_time": 3, "end_time": 8}
    assert cue(**on, loop_start=1, loop_end=None).section_bounds() == (3, 8)
    assert cue(**on, loop_start=4, loop_end=9).section_bounds() == (4, 8)
    assert cue(**on, loop_start=8, loop_end=8).section_bounds() is None  # empty


def test_show_file_round_trip(tmp_path):
    c = cue(loop_section=True, loop_start=1.5, loop_end=4.25, loop_first=FIRST_INTRO,
            loop_after=AFTER_OUTRO)  # fmt: skip
    back = cue_from_dict(cue_to_dict(c, tmp_path), tmp_path)
    assert (back.loop_section, back.loop_start, back.loop_end) == (True, 1.5, 4.25)
    assert (back.loop_first, back.loop_after) == (FIRST_INTRO, AFTER_OUTRO)


def test_old_or_odd_show_files_get_safe_defaults(tmp_path):
    back = cue_from_dict({"file": "x.wav", "loop_first": "??", "loop_after": None}, tmp_path)
    assert not back.loop_section and back.loop_end is None
    assert (back.loop_first, back.loop_after) == (FIRST_FULL, AFTER_STOP)


# ------------------------------------------------------------------ in the window
@pytest.fixture
def window(make_window, tmp_path):
    path = tmp_path / "bed.wav"
    sf.write(path, np.full((48000 * 4, 2), 0.1, np.float32), 48000)  # 4 s
    w = make_window()
    w.add_files([path])
    w.cue_view.select_row(0)
    w.cache.get(w.cue_model.cue_at(0).file_path)  # wait for the decode, then show it
    w._load_inspector_audio()
    return w


def test_cue_becomes_a_section_voice(window):
    w = window
    c = w.cue_model.cue_at(0)
    for attr, value in (("loop_section", True), ("loop_start", 1.0), ("loop_end", 2.0),
                        ("repeats", 2), ("loop_after", AFTER_OUTRO)):  # fmt: skip
        w.edit_cue(c.id, attr, value, attr)
    v = w.player._make_voice(c)
    sr = v.samplerate
    assert (v.loop_start_frame, v.loop_end_frame) == (sr, 2 * sr)
    assert v.first_end_frame == v.end_frame and v.outro
    # 4 s whole + 2 × 1 s section + 2 s outro
    assert v.remaining_frames() == 8 * sr


def test_progress_shows_time_left_across_segments(window):
    w = window
    c = w.cue_model.cue_at(0)
    for attr, value in (("loop_section", True), ("loop_start", 1.0), ("loop_end", 2.0),
                        ("repeats", 1)):  # fmt: skip
        w.edit_cue(c.id, attr, value, attr)
    w.go()
    sr = w.engine.samplerate
    w.engine.mixer.render(sr)  # 1 s in
    w._tick()
    voice_id = w.transport.running[0].voice_id
    assert w.player.progress(voice_id).remaining == pytest.approx(4.0, abs=0.02)


def test_editor_checkbox_fields_and_table(window):
    w = window
    tl = w.inspector.time_loops
    c = w.cue_model.cue_at(0)
    assert not tl.loop_start.isEnabled()
    tl.loop_section.setChecked(True)
    assert c.loop_section and tl.loop_start.isEnabled()
    tl.loop_start.setValue(1.0)
    tl.loop_end.setValue(3.0)
    assert (c.loop_start, c.loop_end) == (1.0, 3.0)
    tl.loop_end.setValue(4.0)  # the cue's End: follows End from now on
    assert c.loop_end is None
    tl.loop_first.setCurrentIndex(tl.loop_first.findData(FIRST_INTRO))
    assert c.loop_first == FIRST_INTRO
    w.edit_cue(c.id, "repeats", 2, "repeats")
    assert w.cue_model.data(w.cue_model.index(0, Col.REPEATS)) == "↻ 2 §"
    wave = tl.waveform
    assert wave.loop_on and wave.loop_start_eff == 1.0 and wave.loop_end_eff == 4.0
    w.undo_stack.undo()  # repeats
    w.undo_stack.undo()  # first play
    w.undo_stack.undo()  # loop end → None
    w.undo_stack.undo()  # loop end
    w.undo_stack.undo()  # loop start
    w.undo_stack.undo()  # the checkbox
    assert not c.loop_section and not tl.loop_section.isChecked()


def test_end_fade_handle_sits_at_loop_end_when_the_cue_stops_there(window):
    w = window
    c = w.cue_model.cue_at(0)
    for attr, value in (("loop_section", True), ("loop_start", 1.0), ("loop_end", 2.0)):
        w.edit_cue(c.id, attr, value, attr)
    wave = w.inspector.time_loops.waveform
    assert wave.fade_end == 2.0
    w.edit_cue(c.id, "loop_after", AFTER_OUTRO, "after")
    assert wave.fade_end == 4.0


# ------------------------------------------------------------------ dragging the lines
def drag(wave, x0: float, x1: float) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    y = wave.height() // 2
    no = Qt.KeyboardModifier.NoModifier
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, no, QPoint(int(x0), y))
    QTest.mouseMove(wave, QPoint(int(x1), y))
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, no, QPoint(int(x1), y))


@pytest.fixture
def shown(window, qapp):
    window.inspector.setCurrentIndex(1)
    window.resize(1280, 720)
    window.show()
    qapp.processEvents()
    return window


def test_ticking_the_box_places_the_loop_inside_the_track_in_one_undo_step(shown):
    w = shown
    c = w.cue_model.cue_at(0)
    w.inspector.time_loops.loop_section.setChecked(True)
    assert c.loop_section
    assert c.loop_start == pytest.approx(4 / 3, abs=1e-3)  # a third into the 4 s file
    assert c.loop_end == pytest.approx(8 / 3, abs=1e-3)
    w.undo_stack.undo()
    assert (c.loop_section, c.loop_start, c.loop_end) == (False, 0.0, None)


def test_dragging_loop_start_never_moves_the_track_start(shown):
    w = shown
    c = w.cue_model.cue_at(0)
    w.edit_cue_group(c.id, [("loop_section", True), ("start_time", 1.0)], "setup")
    wave = w.inspector.time_loops.waveform
    x = wave.x_of(1.0)  # Loop start (0, clamped) and Start share this line
    drag(wave, x, x + 120)  # to the right: the loop start
    assert c.start_time == 1.0 and c.loop_start > 1.0
    w.edit_cue(c.id, "loop_start", 1.0, "back")
    drag(wave, x, x - 60)  # to the left: the track's Start
    assert c.start_time < 1.0 and c.loop_start == 1.0


def test_dragging_loop_end_never_moves_the_track_end(shown):
    w = shown
    c = w.cue_model.cue_at(0)
    w.edit_cue_group(c.id, [("loop_section", True), ("end_time", 3.0)], "setup")
    wave = w.inspector.time_loops.waveform
    x = wave.x_of(3.0)  # Loop end (None → End) and End share this line
    drag(wave, x, x - 120)  # to the left: the loop end
    assert c.end_time == 3.0 and c.loop_end < 3.0
    w.edit_cue(c.id, "loop_end", None, "back")
    drag(wave, x, x + 60)  # to the right: the track's End
    assert c.end_time > 3.0 and c.loop_end is None
