"""Phase 4: inspector edits, live level changes, master fader, meter, show-file master."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from winq.audio.engine import Mixer, Play
from winq.audio.voice import Voice
from winq.model.show_file import load_show, save_show
from winq.ui.level_control import LevelControl, PeakMeter


def write_tone(path: Path, seconds: float, value: float = 0.5, sr: int = 48000) -> Path:
    sf.write(path, np.full((int(seconds * sr), 1), value, dtype=np.float32), sr)
    return path


def render(w, seconds: float) -> np.ndarray:
    n = int(seconds * w.engine.samplerate / 512) + 1
    return np.concatenate([w.engine.mixer.render(512) for _ in range(n)])


@pytest.fixture
def window(make_window, tmp_path):
    w = make_window()
    w.add_files([write_tone(tmp_path / "a.wav", 2.0), write_tone(tmp_path / "b.wav", 2.0)])
    w.cue_view.select_row(0)
    return w


# ------------------------------------------------------------------- inspector
def test_inspector_follows_selection(window):
    w = window
    assert w.inspector.name.text() == "a"
    w.cue_view.select_row(1)
    assert w.inspector.name.text() == "b"
    assert w.inspector.isEnabled()


def test_inspector_text_edits_are_undoable(window):
    w = window
    w.inspector.name.setText("Opening music")
    w.inspector.name.editingFinished.emit()
    assert w.cue_model.cue_at(0).name == "Opening music"
    assert w.transport_bar.standby.text().endswith("Opening music")
    w.undo_stack.undo()
    assert w.cue_model.cue_at(0).name == "a"
    assert w.inspector.name.text() == "a"  # inspector refreshed by the undo


def test_notes_typing_session_is_one_undo_step(window):
    w = window
    before = w.undo_stack.count()
    w.inspector._new_notes_session()
    for text in ("H", "Hi", "Hi!"):
        w.inspector.notes.setPlainText(text)
    assert w.cue_model.cue_at(0).notes == "Hi!"
    assert w.undo_stack.count() == before + 1
    w.inspector._new_notes_session()  # focus left and came back
    w.inspector.notes.setPlainText("Hi! Again")
    assert w.undo_stack.count() == before + 2


def test_slider_drag_is_one_undo_step(window):
    w = window
    before = w.undo_stack.count()
    lc = w.inspector.level
    lc.slider.setSliderDown(True)  # start of a drag → one gesture
    lc._new_gesture()
    for v in (-2, -6, -12):
        lc.slider.setValue(v)
    lc.slider.setSliderDown(False)
    assert w.cue_model.cue_at(0).level_db == -6.0  # slider steps are 0.5 dB
    assert w.undo_stack.count() == before + 1
    w.undo_stack.undo()
    assert w.cue_model.cue_at(0).level_db == 0.0
    assert lc.level() == 0.0


def test_level_spinbox_and_minus_inf(window):
    w = window
    lc = w.inspector.level
    lc.spin.setValue(-3.5)
    assert w.cue_model.cue_at(0).level_db == -3.5
    lc.spin.setValue(lc.spin.minimum())
    assert lc.spin.text() == "-inf"
    assert w.cue_model.data(w.cue_model.index(0, 4)) == "-inf"


# ------------------------------------------------------------------- live level
def test_level_change_applies_to_playing_copies(window):
    w = window
    w.go()  # cue a, 0 dB, tone at 0.5
    out = render(w, 0.05)
    assert np.abs(out[-100:]).max() == pytest.approx(0.5, abs=1e-3)
    w.edit_cue(w.cue_model.cue_at(0).id, "level_db", -6.0, "level")
    out = render(w, 0.05)
    assert np.abs(out[-100:]).max() == pytest.approx(0.5 * 10 ** (-6 / 20), abs=1e-3)
    w.undo_stack.undo()  # undo is live too
    out = render(w, 0.05)
    assert np.abs(out[-100:]).max() == pytest.approx(0.5, abs=1e-3)


def test_new_copies_use_the_current_level(window):
    w = window
    w.edit_cue(w.cue_model.cue_at(0).id, "level_db", -60.0, "level")  # -inf
    w.go()
    out = render(w, 0.05)
    assert np.abs(out).max() == 0.0


# ------------------------------------------------------------------- master
def test_master_fader_is_live_undoable_and_marks_unsaved(window):
    w = window
    w.undo_stack.setClean()
    w.go()
    w.transport_bar.master.spin.setValue(-6.0)
    assert w.master_db == -6.0
    assert not w.undo_stack.isClean()
    out = render(w, 0.05)
    assert np.abs(out[-100:]).max() == pytest.approx(0.5 * 10 ** (-6 / 20), abs=1e-3)
    w.undo_stack.undo()
    assert w.master_db == 0.0 and w.transport_bar.master.level() == 0.0
    assert w.undo_stack.isClean()


def test_master_range_is_minus_inf_to_plus_6(window):
    m = window.transport_bar.master
    assert m.spin.maximum() == 6.0
    m.spin.setValue(20)
    assert m.level() == 6.0


def test_master_saved_and_loaded_with_show(window, make_window, tmp_path):
    w = window
    w.transport_bar.master.spin.setValue(-4.5)
    assert w._write_show(tmp_path / "s.winq")
    assert load_show(tmp_path / "s.winq").master_db == -4.5

    w2 = make_window()
    w2.load_show_file(tmp_path / "s.winq")
    assert w2.master_db == -4.5
    assert w2.transport_bar.master.level() == -4.5
    assert w2.undo_stack.isClean()


def test_new_show_resets_master(window):
    w = window
    w.transport_bar.master.spin.setValue(-10)
    w.undo_stack.setClean()
    w.new_show()
    assert w.master_db == 0.0
    assert not w.inspector.isEnabled()


def test_show_file_master_roundtrip(tmp_path):
    save_show(tmp_path / "x.winq", [], master_db=3.0)
    assert load_show(tmp_path / "x.winq").master_db == 3.0


# ------------------------------------------------------------------- meter
def test_mixer_take_peaks_per_channel():
    m = Mixer(1000)
    buf = np.zeros((100, 2), dtype=np.float32)
    buf[10, 0] = 0.9
    buf[50, 1] = -0.3
    m.send(Play(Voice(1, buf, 1000)))
    m.render(64)
    m.render(64)
    left, right = m.take_peaks()
    assert left == pytest.approx(0.9) and right == pytest.approx(0.3)
    assert m.take_peaks() == (0.0, 0.0)  # reset after taking


def test_peak_meter_attack_fall_and_clip(qtbot):
    meter = PeakMeter()
    qtbot.addWidget(meter)
    meter.push((1.0, 0.1), 0.03)
    left, right = meter.levels()
    assert left == pytest.approx(0.0) and right == pytest.approx(-20.0)
    assert meter.clipped
    meter.push((0.0, 0.0), 0.5)  # falls slowly, not instantly
    assert meter.levels()[0] == pytest.approx(-12.0)
    meter.mousePressEvent(None)
    assert not meter.clipped


def test_meter_is_fed_from_the_engine(window):
    w = window
    w.go()
    render(w, 0.05)
    w._tick()
    assert w.transport_bar.meter.levels()[0] > -10


def test_level_control_double_click_resets_to_zero(qtbot):
    lc = LevelControl(max_db=12)
    qtbot.addWidget(lc)
    lc.set_level(-20)
    with qtbot.waitSignal(lc.level_changed) as sig:
        qtbot.mouseDClick(lc.slider, qtbot_left())
    assert sig.args[0] == 0.0


def qtbot_left():
    from PySide6.QtCore import Qt

    return Qt.MouseButton.LeftButton
