"""Qt-level tests: table model, undo/redo, drag-and-drop signals, main window adding cues."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QByteArray, QMimeData, QModelIndex, Qt, QUrl
from PySide6.QtGui import QUndoStack

from winq.model.cue import INFINITE_REPEATS, AudioCue
from winq.ui.commands import AddCuesCommand, MoveCueCommand, RemoveCueCommand
from winq.ui.cue_table import (
    ROW_MIME,
    Col,
    CueTableModel,
    format_duration,
    format_level,
    format_repeats,
    supported_audio_files,
)


def cue(name: str) -> AudioCue:
    return AudioCue(file_path=Path(f"C:/audio/{name}.wav"), name=name)


def names(model: CueTableModel) -> list[str]:
    return [c.name for c in model.cue_list]


@pytest.fixture
def model(qtbot):
    stack = QUndoStack()
    m = CueTableModel(stack)
    m._stack_ref = stack  # keep alive
    return m


def test_formatting():
    assert format_duration(None) == "?"
    assert format_duration(0) == "0:00.0"
    assert format_duration(83.46) == "1:23.5"
    assert format_level(0) == "0 dB"
    assert format_level(-6) == "-6 dB"
    assert format_level(-3.5) == "-3.5 dB"
    assert format_level(2) == "+2 dB"
    assert format_level(-80) == "-inf"
    assert format_repeats(0, False) == ""
    assert format_repeats(2, False) == "↻ 2"
    assert format_repeats(INFINITE_REPEATS, True) == "∞"


def test_add_remove_move_with_undo(model):
    stack = model.undo_stack
    stack.push(AddCuesCommand(model, 0, [cue("a"), cue("b"), cue("c")]))
    stack.push(AddCuesCommand(model, 1, [cue("x")]))
    assert names(model) == ["a", "x", "b", "c"]
    stack.push(RemoveCueCommand(model, 0))
    assert names(model) == ["x", "b", "c"]
    stack.push(MoveCueCommand(model, 0, 2))
    assert names(model) == ["b", "c", "x"]
    stack.undo()
    assert names(model) == ["x", "b", "c"]
    stack.undo()
    assert names(model) == ["a", "x", "b", "c"]
    stack.undo()
    assert names(model) == ["a", "b", "c"]
    stack.redo()
    stack.redo()
    stack.redo()
    assert names(model) == ["b", "c", "x"]


def test_inline_edit_is_undoable(model):
    model.undo_stack.push(AddCuesCommand(model, 0, [cue("a")]))
    idx = model.index(0, Col.NAME)
    assert model.setData(idx, "  Opening  ")
    assert model.data(idx) == "Opening"
    assert model.undo_stack.undoText() == "Change name"
    assert not model.setData(idx, "Opening")  # unchanged: no extra undo step
    model.undo_stack.undo()
    assert model.data(idx) == "a"
    assert not model.flags(model.index(0, Col.DURATION)) & Qt.ItemFlag.ItemIsEditable


def test_missing_file_is_flagged(model):
    model.undo_stack.push(AddCuesCommand(model, 0, [cue("gone")]))
    idx = model.index(0, Col.FILE)
    assert model.data(idx, Qt.ItemDataRole.ForegroundRole) is not None
    assert "Missing" in model.data(idx, Qt.ItemDataRole.ToolTipRole)


def test_internal_drop_requests_move(model, qtbot):
    model.undo_stack.push(AddCuesCommand(model, 0, [cue("a"), cue("b"), cue("c")]))
    mime = model.mimeData([model.index(0, 0)])
    assert bytes(mime.data(ROW_MIME)) == b"0"
    with qtbot.waitSignal(model.move_requested) as sig:
        handled = model.dropMimeData(mime, Qt.DropAction.MoveAction, 3, 0, QModelIndex())
    assert sig.args == [0, 2]  # dropped after the last row → ends up last
    assert handled is False  # Qt must not remove the source row itself


def test_drop_onto_own_position_is_ignored(model, qtbot):
    model.undo_stack.push(AddCuesCommand(model, 0, [cue("a"), cue("b")]))
    mime = QMimeData()
    mime.setData(ROW_MIME, QByteArray(b"1"))
    with qtbot.assertNotEmitted(model.move_requested):
        model.dropMimeData(mime, Qt.DropAction.MoveAction, 2, 0, QModelIndex())


def test_external_file_drop_emits_paths(model, qtbot, tmp_path):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "a.wav"))])
    with qtbot.waitSignal(model.files_dropped) as sig:
        assert model.dropMimeData(mime, Qt.DropAction.CopyAction, -1, -1, QModelIndex())
    assert sig.args == [[tmp_path / "a.wav"], 0]


def test_supported_files_expand_folders(tmp_path):
    (tmp_path / "b.mp3").touch()
    (tmp_path / "a.WAV").touch()
    (tmp_path / "notes.txt").touch()
    ok, rejected = supported_audio_files([tmp_path])
    assert [p.name for p in ok] == ["a.WAV", "b.mp3"]
    assert [p.name for p in rejected] == ["notes.txt"]


def write_wav(path: Path, seconds: float) -> Path:
    sf.write(path, np.zeros((int(seconds * 8000), 1), dtype=np.float32), 8000)
    return path


def test_main_window_adds_numbered_cues_after_selection(make_window, tmp_path):
    w = make_window()
    a = write_wav(tmp_path / "Pre-show.wav", 2.0)
    b = write_wav(tmp_path / "Thunder.wav", 0.5)
    w.add_files([a, b])
    cues = w.cue_model.cue_list
    assert [(c.number, c.name) for c in cues] == [("1", "Pre-show"), ("2", "Thunder")]
    assert cues[0].file_duration == pytest.approx(2.0)
    assert w.windowTitle().endswith("•")  # unsaved

    w.cue_view.select_row(0)
    w.add_files([write_wav(tmp_path / "Door.wav", 0.2)])
    assert [c.name for c in cues] == ["Pre-show", "Door", "Thunder"]
    assert cues[1].number == "3"
    assert w.cue_view.selected_row() == 1

    w.delete_selected_cue()
    assert [c.name for c in cues] == ["Pre-show", "Thunder"]
    w.undo_stack.undo()
    assert [c.name for c in cues] == ["Pre-show", "Door", "Thunder"]


def test_main_window_save_and_reopen(make_window, tmp_path):
    w = make_window()
    w.add_files([write_wav(tmp_path / "x.wav", 1.0)])
    assert w._write_show(tmp_path / "show.winq")
    assert w.windowTitle() == "WinQ — show"

    w2 = make_window()
    assert w2.load_show_file(tmp_path / "show.winq")
    (c,) = w2.cue_model.cue_list
    assert c.name == "x" and c.file_duration == pytest.approx(1.0)
    assert w2.cue_view.selected_row() == 0
