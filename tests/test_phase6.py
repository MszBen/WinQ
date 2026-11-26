"""Phase 6: Show mode, preferences, relink, recent shows, autosave + recovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QModelIndex, QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from winq.audio.devices import OutputDevice
from winq.ui.app_settings import AppPrefs
from winq.ui.cue_table import Col
from winq.ui.prefs_dialog import PreferencesDialog, device_key


def write_tone(path: Path, seconds: float = 0.2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.full((int(seconds * 48000), 1), 0.3, dtype=np.float32), 48000)
    return path


@pytest.fixture
def window(make_window, tmp_path):
    w = make_window()
    w.add_files([write_tone(tmp_path / "a.wav"), write_tone(tmp_path / "b.wav")])
    w.cue_view.select_row(0)
    return w


# ------------------------------------------------------------------- Show mode
def test_show_mode_locks_editing_but_not_playback(window, tmp_path):
    w = window
    w.set_show_mode(True)
    assert "[SHOW MODE]" in w.windowTitle()
    assert w.transport_bar.mode_button.text() == "SHOW"
    m = w.cue_model
    assert not m.flags(m.index(0, Col.NAME)) & Qt.ItemFlag.ItemIsEditable
    assert not m.flags(m.index(0, Col.NAME)) & Qt.ItemFlag.ItemIsDragEnabled
    assert not m.setData(m.index(0, Col.NAME), "X")
    assert not m.canDropMimeData(None, Qt.DropAction.CopyAction, 0, 0, QModelIndex())
    assert not w.inspector.isEnabled()

    before = w.undo_stack.count()
    w.delete_selected_cue()
    w.add_files([write_tone(tmp_path / "c.wav")])
    w.edit_cue(m.cue_at(0).id, "name", "nope", "name")
    assert m.rowCount() == 2 and m.cue_at(0).name == "a"
    assert w.undo_stack.count() == before
    assert all(not a.isEnabled() for a in w._edit_actions)

    w.go()  # playback still works
    assert len(w.transport.running) == 1
    w.transport_bar.master.spin.setValue(-6)  # so does the master
    assert w.master_db == -6

    w.toggle_show_mode()
    assert not w.show_mode and w.inspector.isEnabled()
    assert all(
        a.isEnabled() for a in w._edit_actions if a.text() != "&Undo" and "Redo" not in a.text()
    )


def test_show_mode_button_and_shortcut_action(window):
    w = window
    w.transport_bar.mode_button.click()
    assert w.show_mode and w._show_mode_action.isChecked()
    w._show_mode_action.trigger()
    assert not w.show_mode
    assert w._show_mode_action.shortcut().toString() == "Ctrl+M"


def test_show_mode_stops_preview(window, qtbot):
    w = window
    qtbot.waitUntil(lambda: (w._tick(), w.inspector.time_loops.waveform.has_audio)[1])
    w.inspector.time_loops.preview.click()
    assert w._preview_voice is not None
    w.set_show_mode(True)
    assert w._preview_voice is None


# ------------------------------------------------------------------- preferences
def test_prefs_dialog_values(qtbot):
    devs = [
        OutputDevice(1, "Speakers", "MME", 2, 44100.0),
        OutputDevice(7, "Speakers", "Windows WASAPI", 2, 48000.0),
    ]
    d = PreferencesDialog(devs, 1, AppPrefs(panic_fade=0.5))
    qtbot.addWidget(d)
    assert d.device.itemData(0) == 7  # WASAPI listed first
    assert d.selected_device() == 1  # current device preselected
    d.panic_fade.setValue(1.25)
    assert d.selected_panic_fade() == 1.25
    assert device_key(devs[1]) == "Windows WASAPI|Speakers"


def test_panic_fade_preference_is_remembered(make_window, tmp_path):
    w = make_window(tmp_path)
    w.settings.setValue("panic_fade", 2.0)
    w.settings.sync()
    w2 = make_window(tmp_path)
    assert w2.panic_fade == 2.0


def test_unknown_saved_device_falls_back_with_note(make_window, tmp_path):
    s = QSettings(str(tmp_path / "winq.ini"), QSettings.Format.IniFormat)
    s.setValue("audio/device", "Nope API|Unplugged Interface")
    s.sync()
    w = make_window(tmp_path)
    assert w.engine is not None  # default device instead
    idx, note = w._saved_device_index()
    assert idx is None and "Unplugged Interface" in note


def test_switch_output_device_keeps_show(window):
    w = window
    w.go()
    assert w.switch_output_device(None, start=False)
    assert w.transport.running == ()  # everything stopped
    assert w.cue_model.rowCount() == 2
    w.go()  # and it still plays on the new engine
    assert len(w.transport.running) == 1


# ------------------------------------------------------------------- relink
def test_relink_one_and_offer_the_rest(window, tmp_path):
    w = window
    for c in w.cue_model.cue_list:  # both files "moved"
        c.file_path = tmp_path / "old" / c.file_path.name
        c.file_duration = None
    new = tmp_path / "new"
    write_tone(new / "a.wav", 0.5)
    write_tone(new / "b.wav", 0.5)
    w.cue_view.select_row(0)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        w.relink_selected_cue(new / "a.wav")
    cues = list(w.cue_model.cue_list)
    assert [c.file_path for c in cues] == [(new / "a.wav").resolve(), (new / "b.wav").resolve()]
    assert all(not c.is_missing for c in cues)
    assert cues[0].file_duration == pytest.approx(0.5)
    assert w.undo_stack.undoText() == "Relink files"
    w.undo_stack.undo()  # one step undoes both
    assert all(c.is_missing for c in w.cue_model.cue_list)


def test_relink_only_selected_when_declined(window, tmp_path):
    w = window
    for c in w.cue_model.cue_list:
        c.file_path = tmp_path / "old" / c.file_path.name
        c.file_duration = None
    write_tone(tmp_path / "new" / "a.wav")
    write_tone(tmp_path / "new" / "b.wav")
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        w.relink_selected_cue(tmp_path / "new" / "a.wav")
    assert not w.cue_model.cue_at(0).is_missing
    assert w.cue_model.cue_at(1).is_missing


# ------------------------------------------------------------------- recent / startup
def test_recent_shows_and_reopen_last(make_window, tmp_path):
    w = make_window(tmp_path)
    w.add_files([write_tone(tmp_path / "x.wav")])
    assert w._write_show(tmp_path / "one.winq")
    assert w._write_show(tmp_path / "two.winq")
    assert w.recent_shows()[:2] == [
        (tmp_path / "two.winq").resolve(),
        (tmp_path / "one.winq").resolve(),
    ]
    w.settings.sync()

    w2 = make_window(tmp_path)  # "next launch"
    w2.startup()
    assert w2.show_path == (tmp_path / "two.winq").resolve()
    assert w2.cue_model.rowCount() == 1


def test_command_line_show_wins_over_last(make_window, tmp_path):
    w = make_window(tmp_path)
    w.add_files([write_tone(tmp_path / "x.wav")])
    w._write_show(tmp_path / "last.winq")
    w.new_show()
    w._write_show(tmp_path / "other.winq")
    w.settings.setValue("last_show", str(tmp_path / "last.winq"))
    w.settings.sync()
    w2 = make_window(tmp_path)
    w2.startup(tmp_path / "other.winq")
    assert w2.show_path.name == "other.winq"


# ------------------------------------------------------------------- autosave
def test_autosave_only_when_unsaved_and_never_touches_show(window, tmp_path):
    w = window
    assert w._write_show(tmp_path / "s.winq")
    original = (tmp_path / "s.winq").read_text()
    assert not w.autosave()  # clean: nothing to back up
    w.edit_cue(w.cue_model.cue_at(0).id, "name", "Changed", "name")
    assert w.autosave()
    assert w._autosave_file.exists()
    assert (tmp_path / "s.winq").read_text() == original
    w._write_show(tmp_path / "s.winq")
    assert not w._autosave_file.exists()  # saving removes the backup


def test_crash_recovery_flow(make_window, tmp_path):
    shared = tmp_path / "settings"
    shared.mkdir()
    w = make_window(shared)
    w.add_files([write_tone(tmp_path / "a.wav")])
    w._write_show(tmp_path / "show.winq")
    w.edit_cue(w.cue_model.cue_at(0).id, "name", "Unsaved name", "name")
    assert w.autosave()
    # Simulate a crash: the backup stays, but under another process's name.
    crashed = w._autosave_file.with_name("session-999999.winq")
    w._autosave_file.rename(crashed)

    w2 = make_window(shared)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        w2.startup()
    assert w2.cue_model.cue_at(0).name == "Unsaved name"
    assert w2.show_path == (tmp_path / "show.winq")
    assert not w2.undo_stack.isClean()  # still needs saving
    assert not crashed.exists()


def test_declining_recovery_discards_backup_and_opens_last(make_window, tmp_path):
    shared = tmp_path / "settings"
    shared.mkdir()
    w = make_window(shared)
    w.add_files([write_tone(tmp_path / "a.wav")])
    w._write_show(tmp_path / "show.winq")
    w.edit_cue(w.cue_model.cue_at(0).id, "name", "Lost", "name")
    w.autosave()
    crashed = w._autosave_file.with_name("session-999998.winq")
    w._autosave_file.rename(crashed)
    w.settings.sync()

    w2 = make_window(shared)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        w2.startup()
    assert not crashed.exists()
    assert w2.cue_model.cue_at(0).name == "a"  # last saved show reopened
