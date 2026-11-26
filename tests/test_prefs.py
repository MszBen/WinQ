"""Preferences: storage, the dialog, and each option's effect on the main window."""

from __future__ import annotations

import os

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QSettings

from winq.audio.devices import OutputDevice
from winq.audio.test_tone import tone_buffer
from winq.control.transport import RunningInstance
from winq.model.cue import CONTINUE, FOLLOW
from winq.ui import main_window as mw
from winq.ui.active_cues import format_remaining
from winq.ui.app_settings import AppPrefs, apply_ui_scale, load_prefs, save_prefs
from winq.ui.prefs_dialog import PreferencesDialog


def wav(path, seconds=1.0, sr=48000):
    sf.write(path, np.full((int(seconds * sr), 2), 0.1, np.float32), sr)
    return path


@pytest.fixture
def window(make_window, tmp_path):
    w = make_window()
    w.add_files([wav(tmp_path / "a.wav"), wav(tmp_path / "b.wav")])
    w.cue_view.select_row(0)
    return w


@pytest.fixture
def awake_calls(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(mw, "set_keep_awake", calls.append)
    return calls


# ------------------------------------------------------------------ storage
def test_prefs_round_trip(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    prefs = AppPrefs(buffer="safe", go_guard=0.5, open_in_show_mode=True, cue_stop_fade=2.5,
                     cue_then=FOLLOW, ui_scale=130, show_notes=False, active_time="elapsed",
                     reopen_last=False, autosave_seconds=0)  # fmt: skip
    save_prefs(s, prefs)
    s.sync()
    again = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    assert load_prefs(again) == prefs


def test_bad_or_missing_values_fall_back_to_defaults(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("prefs/buffer", "huge")
    s.setValue("prefs/go_guard", "0.42")
    s.setValue("prefs/autosave_seconds", "abc")
    s.setValue("panic_fade", "1.5")  # the original key still works
    prefs = load_prefs(s)
    assert (prefs.buffer, prefs.go_guard, prefs.autosave_seconds) == ("low", 0.3, 30)
    assert prefs.panic_fade == 1.5


def test_ui_scale_sets_qt_scale_factor_before_startup(tmp_path, monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    save_prefs(s, AppPrefs(ui_scale=115))
    assert apply_ui_scale(s) == 115
    assert os.environ["QT_SCALE_FACTOR"] == "1.15"


def test_dialog_returns_every_choice(qapp):
    devs = [OutputDevice(1, "Speakers", "Windows WASAPI", 2, 48000.0)]
    d = PreferencesDialog(devs, 1, AppPrefs())
    d.buffer.setCurrentIndex(d.buffer.findData("normal"))
    d.go_guard.setCurrentIndex(d.go_guard.findData(1.0))
    d.cue_fade_in.setValue(0.5)
    d.cue_then.setCurrentIndex(d.cue_then.findData(CONTINUE))
    d.show_notes.setChecked(False)
    d.autosave.setCurrentIndex(d.autosave.findData(300))
    p = d.selected_prefs()
    assert (p.buffer, p.go_guard, p.cue_fade_in, p.cue_then) == ("normal", 1.0, 0.5, CONTINUE)
    assert (p.show_notes, p.autosave_seconds) == (False, 300)
    assert [d.tabs.tabText(i) for i in range(d.tabs.count())] == [
        "Audio", "Show", "New cues", "Display", "Files",
    ]  # fmt: skip


# ------------------------------------------------------------------ show control
def test_double_go_guard_ignores_a_quick_second_go(window):
    w = window
    w.prefs.go_guard = 0.5
    w.go()
    w.go()  # within 0.5 s: ignored
    assert len(w.transport.running) == 1
    assert w.cue_view.selected_row() == 1  # moved on once only
    assert "ignored" in w.transport_bar.warning.text()
    w._last_go -= 1.0  # pretend a second has passed
    w.go()
    assert len(w.transport.running) == 2


def test_quit_asks_while_playing_or_in_show_mode(window, monkeypatch, awake_calls):
    w = window
    w.undo_stack.setClean()
    w.confirm_quit = True
    asked: list[str] = []

    def keep_running(text):
        asked.append(text)
        return False

    monkeypatch.setattr(w, "_ask_quit", keep_running)
    assert w._confirm_quit_during_show()  # idle, Edit mode: no question
    w.go()
    assert not w._confirm_quit_during_show()
    assert "Cues are playing" in asked[-1]
    w.panic()
    for _ in range(10):
        w.engine.mixer.render(4800)
        w._tick()
    w.set_show_mode(True)
    assert not w._confirm_quit_during_show()
    assert "Show mode" in asked[-1]
    w.set_show_mode(False)


def test_show_mode_keeps_the_pc_awake(window, awake_calls):
    window.set_show_mode(True)
    window.set_show_mode(False)
    assert awake_calls == [True, False]


def test_open_in_show_mode(window, tmp_path, awake_calls):
    w = window
    path = tmp_path / "show.winq"
    assert w._write_show(path)
    w.prefs.open_in_show_mode = True
    assert w.load_show_file(path)
    assert w.show_mode
    w.set_show_mode(False)


# ------------------------------------------------------------------ display
def test_notes_show_in_the_go_bar(window):
    w = window
    cue = w.cue_model.cue_at(0)
    w.edit_cue(cue.id, "notes", "Wait for the door\n\nthen go", "notes")
    assert w.transport_bar.hint.text() == "✎ Wait for the door · then go"
    w.apply_prefs(AppPrefs(show_notes=False, go_guard=0.0))
    assert w.transport_bar.hint.text() == ""


def test_active_cues_can_show_elapsed_time():
    from pathlib import Path

    from winq.audio.cue_player import Progress
    from winq.model.cue import AudioCue

    r = RunningInstance(voice_id=1, cue=AudioCue(file_path=Path("x.wav")), looping=False)
    p = Progress(elapsed=4.1, pass_fraction=0.2, remaining=12.3)
    assert format_remaining(p, r, "remaining") == "-0:12.3"
    assert format_remaining(p, r, "elapsed") == "+0:04.1"


# ------------------------------------------------------------------ new cues
def test_new_cues_use_the_defaults(make_window, tmp_path):
    w = make_window()
    w.prefs = AppPrefs(cue_level_db=-6.0, cue_fade_in=0.5, cue_end_fade=1.0,
                       cue_stop_fade=3.0, cue_then=FOLLOW, go_guard=0.0)  # fmt: skip
    w.add_files([wav(tmp_path / "long.wav", 4.0), wav(tmp_path / "short.wav", 0.6)])
    long, short = w.cue_model.cue_at(0), w.cue_model.cue_at(1)
    assert (long.level_db, long.fade_in, long.end_fade) == (-6.0, 0.5, 1.0)
    assert (long.stop_fade, long.continue_mode) == (3.0, FOLLOW)
    # 1.5 s of fades don't fit a 0.6 s file: both shrink in proportion.
    assert short.fade_in + short.end_fade == pytest.approx(0.6, abs=0.002)
    assert short.end_fade == pytest.approx(2 * short.fade_in, abs=0.002)


# ------------------------------------------------------------------ audio
def test_buffer_choice_reaches_the_engine(make_window):
    w = make_window()
    w.prefs.buffer = "safe"
    w.switch_output_device(None, start=False)
    assert w.engine.latency == 0.15


def test_test_tone_plays_left_then_right(window):
    sr = window.engine.samplerate
    buf = tone_buffer(sr)
    half = buf.shape[0] // 2
    assert np.abs(buf[: half - sr // 10, 0]).max() > 0.1
    assert np.abs(buf[: half - sr // 10, 1]).max() == 0
    assert np.abs(buf[half + sr // 10 :, 1]).max() > 0.1
    assert np.abs(buf[half + sr // 10 :, 0]).max() == 0
    window.play_test_tone()
    out = window.engine.mixer.render(sr // 2)
    assert np.abs(out[:, 0]).max() > 0.05
    for _ in range(4):
        window.engine.mixer.render(sr)
        window._tick()
    assert window._test_tone is None  # finished and forgotten
    assert window.transport.running == ()  # never part of GO/Esc


# ------------------------------------------------------------------ files
def test_reopen_last_can_be_turned_off(make_window, tmp_path, window):
    path = tmp_path / "last.winq"
    assert window._write_show(path)
    w2 = make_window(settings_dir=None)
    w2.settings.setValue("last_show", str(path))
    w2.prefs.reopen_last = False
    w2.startup()
    assert w2.show_path is None
    w2.prefs.reopen_last = True
    w2.startup()
    assert w2.show_path == path


def test_autosave_interval(window):
    w = window
    w.apply_prefs(AppPrefs(autosave_seconds=0, go_guard=0.0))
    assert not w._autosave_timer.isActive()
    w.apply_prefs(AppPrefs(autosave_seconds=60, go_guard=0.0))
    assert w._autosave_timer.interval() == 60_000
