"""Phase 5: waveform editing, time fields, repeats, stop fade, preview, playheads."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from winq.audio.voice import Voice
from winq.model.cue import INFINITE_REPEATS
from winq.ui.time_edit import format_time, parse_time
from winq.ui.waveform import WaveformView, nice_step

SR = 48000


def write_tone(path: Path, seconds: float, value: float = 0.5) -> Path:
    sf.write(path, np.full((int(seconds * SR), 1), value, dtype=np.float32), SR)
    return path


def render(w, seconds: float) -> np.ndarray:
    n = int(seconds * w.engine.samplerate / 512) + 1
    return np.concatenate([w.engine.mixer.render(512) for _ in range(n)])


def wait_for_waveform(w, qtbot) -> None:
    qtbot.waitUntil(lambda: (w._tick(), w.inspector.time_loops.waveform.has_audio)[1], timeout=5000)


@pytest.fixture
def window(make_window, tmp_path, qtbot):
    w = make_window()
    w.add_files([write_tone(tmp_path / "a.wav", 10.0), write_tone(tmp_path / "b.wav", 4.0)])
    w.cue_view.select_row(0)
    wait_for_waveform(w, qtbot)
    return w


def cue0(w):
    return w.cue_model.cue_at(0)


# ------------------------------------------------------------------- pure helpers
@pytest.mark.parametrize(
    ("text", "seconds"),
    [("1:23.45", 83.45), ("0:05", 5.0), ("12.5", 12.5), ("2:", 120.0), ("abc", None), ("", None)],
)
def test_parse_time(text, seconds):
    assert parse_time(text) == (pytest.approx(seconds) if seconds is not None else None)


def test_format_time():
    assert format_time(83.456) == "1:23.46"
    assert format_time(0) == "0:00.00"


def test_nice_step():
    assert nice_step(10, 5) == 2
    assert nice_step(0.3, 6) == 0.05
    assert nice_step(600, 10) == 100


def test_voice_can_begin_mid_region():
    buf = np.stack([np.arange(100, dtype=np.float32)] * 2, axis=1)
    v = Voice(1, buf, 1000, start_frame=10, end_frame=20, begin_frame=15, repeats=1)
    out = v.render(30)[:, 0]
    np.testing.assert_array_equal(out[:5], np.arange(15, 20))  # rest of first pass
    np.testing.assert_array_equal(out[5:15], np.arange(10, 20))  # repeat starts at Start
    with pytest.raises(ValueError):
        Voice(1, buf, 1000, start_frame=10, end_frame=20, begin_frame=20)


# ------------------------------------------------------------------- waveform widget
@pytest.fixture
def wave(qtbot):
    w = WaveformView()
    qtbot.addWidget(w)
    w.resize(1000, 150)
    w.show()
    peaks = np.tile(np.array([[-0.5, 0.5]], dtype=np.float32), (1000, 1))
    w.set_audio(peaks, 0.01, 10.0)  # 10 s file, 100 px per second
    w.set_markers(1.0, 8.0, 0.0, 0.0)
    return w


def drag(widget, x0: int, x1: int, y: int = 60) -> None:
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(x0, y))
    for x in np.linspace(x0, x1, 5):
        QTest.mouseMove(widget, QPoint(int(x), y))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(x1, y))


def test_drag_start_and_end_lines(wave, qtbot):
    edits = []
    wave.edit.connect(lambda *a: edits.append(a))
    drag(wave, 100, 250)
    assert edits[-1][:2] == ("start_time", pytest.approx(2.5, abs=0.02))
    assert len({e[2] for e in edits}) == 1  # one gesture for the whole drag
    drag(wave, 800, 600)
    assert edits[-1][:2] == ("end_time", pytest.approx(6.0, abs=0.02))
    assert edits[-1][2] != edits[0][2]  # new drag, new gesture


def test_end_snaps_to_file_end_as_none(wave):
    edits = []
    wave.edit.connect(lambda *a: edits.append(a))
    drag(wave, 800, 998)
    assert edits[-1][:2] == ("end_time", None)


def test_start_cannot_pass_end(wave):
    edits = []
    wave.edit.connect(lambda *a: edits.append(a))
    drag(wave, 100, 950)
    assert edits[-1][1] == pytest.approx(8.0 - 0.01)


def test_fade_handles(wave):
    edits = []
    wave.edit.connect(lambda *a: edits.append(a))
    drag(wave, 100, 300, y=5)  # fade-in handle sits at Start + 0 at the top
    assert edits[-1][:2] == ("fade_in", pytest.approx(2.0, abs=0.02))
    drag(wave, 800, 500, y=5)  # end-fade handle at End
    assert edits[-1][:2] == ("end_fade", pytest.approx(3.0, abs=0.02))


def test_click_sets_preview_point_and_drag_pans(wave, qtbot):
    with qtbot.waitSignal(wave.preview_from_changed) as sig:
        QTest.mouseClick(wave, Qt.MouseButton.LeftButton, pos=QPoint(400, 60))
    assert sig.args[0] == pytest.approx(4.0, abs=0.02)
    wave.zoom(0.5, 5.0)
    assert wave.view_end - wave.view_start == pytest.approx(5.0)
    v0 = wave.view_start
    drag(wave, 500, 300)  # pan: no edit
    assert wave.view_start > v0
    wave.fit()
    assert (wave.view_start, wave.view_end) == (0.0, 10.0)


def test_zoom_limits(wave):
    for _ in range(100):
        wave.zoom(0.5, 5.0)
    assert wave.view_end - wave.view_start == pytest.approx(0.05)
    for _ in range(100):
        wave.zoom(2.0, 5.0)
    assert (wave.view_start, wave.view_end) == (0.0, 10.0)


# ------------------------------------------------------------------- inspector tab
def test_waveform_drag_edits_cue_and_undoes_in_one_step(window):
    w = window
    tl = w.inspector.time_loops
    tl.waveform.resize(1000, 150)
    before = w.undo_stack.count()
    for v in (0.5, 1.0, 1.5):
        tl.waveform.edit.emit("start_time", v, 7)
    assert cue0(w).start_time == 1.5
    assert w.undo_stack.count() == before + 1
    assert tl.start.value() == pytest.approx(1.5)
    w.undo_stack.undo()
    assert cue0(w).start_time == 0.0
    assert tl.waveform.start == 0.0


def test_time_fields_clamp_and_end_of_file(window):
    w = window
    tl = w.inspector.time_loops
    tl.end.setValue(6.0)
    assert cue0(w).end_time == 6.0
    tl.start.setValue(9.0)  # past End: clamped
    assert cue0(w).start_time == pytest.approx(5.99)
    tl.end.setValue(10.0)  # file length → "end of file"
    assert cue0(w).end_time is None
    tl.fade_in.setValue(50)  # longer than the region: clamped
    assert cue0(w).fade_in == pytest.approx(10.0 - 5.99)
    assert w.cue_model.data(w.cue_model.index(0, 3)) == "0:04.0"  # Duration column


def test_repeats_and_loop_forever(window):
    w = window
    tl = w.inspector.time_loops
    tl.repeats.setValue(2)
    assert cue0(w).repeats == 2
    tl.loop_forever.setChecked(True)
    assert cue0(w).repeats == INFINITE_REPEATS and not tl.repeats.isEnabled()
    assert w.cue_model.data(w.cue_model.index(0, 5)) == "∞"
    tl.loop_forever.setChecked(False)
    assert cue0(w).repeats == 2
    w.undo_stack.undo()
    assert cue0(w).is_infinite and tl.loop_forever.isChecked()


def test_stop_fade_field_is_used_by_escape(window):
    w = window
    w.inspector.time_loops.stop_fade.setValue(0.0)
    assert cue0(w).stop_fade == 0.0
    w.go()
    render(w, 0.02)
    w.stop_oldest()
    render(w, 0.02)  # 0 s → 5 ms de-click only
    w._tick()
    assert w.transport.running == ()


def test_trim_changes_what_plays(window):
    w = window
    tl = w.inspector.time_loops
    tl.start.setValue(9.9)  # 0.1 s left in a 10 s file
    w.go()
    render(w, 0.15)
    w._tick()
    assert w.transport.running == ()


# ------------------------------------------------------------------- preview
def test_preview_plays_outside_transport_and_stops(window):
    w = window
    tl = w.inspector.time_loops
    tl.waveform.set_preview_from(5.0)
    tl.preview.click()
    assert w._preview_voice is not None
    assert tl.preview.isChecked() and "Stop" in tl.preview.text()
    assert w.transport.running == ()  # not in the Esc queue
    out = render(w, 0.05)
    assert np.abs(out).max() > 0.4
    w._tick()
    pos = w.player.position(w._preview_voice)
    assert 5.0 < pos < 5.2
    assert tl.waveform._preview_head == pytest.approx(pos)

    w.stop_oldest()  # Esc does not touch the preview
    assert w._preview_voice is not None
    tl.preview.click()  # stop
    assert w._preview_voice is None and not tl.preview.isChecked()
    render(w, 0.1)
    assert np.abs(w.engine.mixer.render(256)).max() == 0.0


def test_preview_ends_by_itself(window):
    w = window
    tl = w.inspector.time_loops
    tl.waveform.set_preview_from(9.95)
    tl.preview.click()
    render(w, 0.1)
    w._tick()
    assert w._preview_voice is None and not tl.preview.isChecked()


def test_selecting_another_cue_stops_preview(window, qtbot):
    w = window
    w.inspector.time_loops.preview.click()
    assert w._preview_voice is not None
    w.cue_view.select_row(1)
    assert w._preview_voice is None
    wait_for_waveform(w, qtbot)
    assert w.inspector.time_loops.waveform.duration == pytest.approx(4.0)


def test_panic_stops_preview(window):
    w = window
    w.inspector.time_loops.preview.click()
    w.panic()
    render(w, w.panic_fade + 0.05)
    w._tick()
    assert w._preview_voice is None


def test_live_playheads_for_running_copies(window):
    w = window
    w.go()
    w.cue_view.select_row(0)
    render(w, 0.5)
    w._tick()
    heads = w.inspector.time_loops.waveform._playheads
    assert len(heads) == 1 and heads[0] == pytest.approx(0.5, abs=0.02)


def test_missing_file_shows_message(window):
    w = window
    cue0(w).file_duration = None
    w.cue_view.select_row(1)
    w.cue_view.select_row(0)
    wave = w.inspector.time_loops.waveform
    assert not wave.has_audio and "missing" in wave._message
    assert not w.inspector.time_loops.preview.isEnabled()


def test_return_in_a_field_gives_focus_back_to_cue_list(window, qtbot):
    w = window
    w.show()
    tl = w.inspector.time_loops
    w.inspector.setCurrentIndex(1)  # Time & Loops
    tl.stop_fade.setFocus()
    qtbot.waitUntil(tl.stop_fade.hasFocus, timeout=2000)
    qtbot.keyClick(tl.stop_fade, Qt.Key.Key_Return)
    qtbot.waitUntil(w.cue_view.hasFocus, timeout=2000)
