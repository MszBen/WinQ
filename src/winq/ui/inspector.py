"""Inspector for the selected cue: Basics, Time & Loops, Levels tabs.

The inspector never changes a cue directly. It emits `edit_requested`, and the main
window turns that into an undoable EditCueCommand. `set_cue` refreshes the widgets
without emitting anything (on selection change, and after undo/redo).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from winq.audio.loader import LoadedAudio
from winq.model.cue import MAX_LEVEL_DB, AudioCue
from winq.ui.elided_label import ElidedLabel
from winq.ui.level_control import LevelControl
from winq.ui.time_loops import TimeLoopsTab


class _VerticalScroll(QScrollArea):
    """Scrolls only vertically. Its minimum width is the page's own, so the page is
    never cut off on the right (there is no sideways scrolling to reach it)."""

    def minimumSizeHint(self) -> QSize:
        page = self.widget()
        base = super().minimumSizeHint()
        if page is None:
            return base
        width = page.minimumSizeHint().width() + self.verticalScrollBar().sizeHint().width()
        return QSize(width + 2 * self.frameWidth(), base.height())


def _vertical_scroll(page: QWidget) -> QScrollArea:
    """Wrap a tab page so a short window scrolls it vertically, never sideways."""
    area = _VerticalScroll()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(page)
    return area


class _NotesEdit(QPlainTextEdit):
    """Plain text box that reports each focus session, so one typing session undoes at once."""

    focus_in = Signal()

    def focusInEvent(self, event) -> None:  # noqa: ANN001
        self.focus_in.emit()
        super().focusInEvent(event)


class Inspector(QTabWidget):
    # (cue id, attribute, new value, label for the undo menu, merge key or None)
    edit_requested = Signal(str, str, object, str, object)
    # (cue id, [(attribute, new value), ...], label): several edits as one undo step
    edit_group_requested = Signal(str, list, str)
    preview_requested = Signal(str, float)  # cue id, from seconds
    preview_stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inspector")
        self._cue: AudioCue | None = None
        self._notes_session = 0
        self._locked = False

        # Basics ------------------------------------------------------
        self.number = QLineEdit()
        self.number.setMaximumWidth(120)
        self.number.editingFinished.connect(lambda: self._commit_text("number", self.number))
        self.name = QLineEdit()
        self.name.editingFinished.connect(lambda: self._commit_text("name", self.name))
        # Long paths are shortened in the middle (drive … file name); full path in the tooltip.
        self.file_label = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.file_label.setObjectName("fileLabel")
        self.notes = _NotesEdit()
        self.notes.setPlaceholderText("Notes for the operator…")
        self.notes.focus_in.connect(self._new_notes_session)
        self.notes.textChanged.connect(self._commit_notes)

        basics = QWidget()
        form = QFormLayout(basics)
        form.addRow("Number", self.number)
        form.addRow("Name", self.name)
        form.addRow("File", self.file_label)
        form.addRow("Notes", self.notes)
        self.addTab(_vertical_scroll(basics), "Basics")

        # Time & Loops ------------------------------------------------
        self.time_loops = TimeLoopsTab()
        self.time_loops.edit.connect(self._emit)
        self.time_loops.edit_group.connect(self._emit_group)
        self.time_loops.preview_requested.connect(self._preview)
        self.time_loops.preview_stop_requested.connect(self.preview_stop_requested)
        self.addTab(_vertical_scroll(self.time_loops), "Time && Loops")

        # Levels ------------------------------------------------------
        self.level = LevelControl(max_db=MAX_LEVEL_DB)
        self.level.level_changed.connect(self._commit_level)
        hint = QLabel("Changes apply immediately, including to copies of this cue already playing.")
        hint.setObjectName("panelEmpty")
        hint.setWordWrap(True)
        levels = QWidget()
        lv = QVBoxLayout(levels)
        lform = QFormLayout()
        lform.addRow("Level", self.level)
        lv.addLayout(lform)
        lv.addWidget(hint)
        lv.addStretch(1)
        self.addTab(_vertical_scroll(levels), "Levels")

        self.set_cue(None)

    @property
    def cue(self) -> AudioCue | None:
        return self._cue

    def set_cue(self, cue: AudioCue | None) -> None:
        """Show `cue` (or nothing). Doesn't emit edits. Only widgets whose value differs
        are touched, so a field being typed in isn't disturbed."""
        if cue is not self._cue:
            self._new_notes_session()
        self._cue = cue
        self.setEnabled(cue is not None and not self._locked)
        self.time_loops.set_cue(cue)
        widgets = (self.number, self.name, self.notes)
        for w in widgets:
            w.blockSignals(True)
        try:
            if cue is None:
                self.number.clear()
                self.name.clear()
                self.notes.clear()
                self.file_label.clear()
                self.level.set_level(0.0)
                return
            if self.number.text() != cue.number:
                self.number.setText(cue.number)
            if self.name.text() != cue.name:
                self.name.setText(cue.name)
            if self.notes.toPlainText() != cue.notes:
                self.notes.setPlainText(cue.notes)
            missing = " (missing or unreadable)" if cue.is_missing else ""
            self.file_label.setText(f"{cue.file_path}{missing}")
            self.file_label.setProperty("missing", cue.is_missing)
            self.file_label.style().unpolish(self.file_label)
            self.file_label.style().polish(self.file_label)
            self.level.set_level(cue.level_db)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def set_locked(self, locked: bool) -> None:
        """Show mode: the inspector stays visible but can't be edited (or preview)."""
        self._locked = locked
        self.setEnabled(self._cue is not None and not locked)

    def set_audio(self, audio: LoadedAudio | None, message: str = "") -> None:
        """Decoded audio of the shown cue (for the waveform), or a message instead."""
        self.time_loops.set_audio(audio, message)

    def _preview(self, from_seconds: float) -> None:
        if self._cue is not None:
            self.preview_requested.emit(self._cue.id, from_seconds)

    # ----------------------------------------------------------------- commits
    def _emit(self, attr: str, value: Any, label: str, merge_key: object = None) -> None:
        if self._cue is not None and getattr(self._cue, attr) != value:
            self.edit_requested.emit(self._cue.id, attr, value, label, merge_key)

    def _emit_group(self, changes: list, label: str) -> None:
        if self._cue is not None:
            self.edit_group_requested.emit(self._cue.id, changes, label)

    def _commit_text(self, attr: str, edit: QLineEdit) -> None:
        self._emit(attr, edit.text().strip(), attr)

    def _new_notes_session(self) -> None:
        self._notes_session += 1

    def _commit_notes(self) -> None:
        self._emit("notes", self.notes.toPlainText(), "notes", ("notes", self._notes_session))

    def _commit_level(self, db: float, gesture: int) -> None:
        self._emit("level_db", db, "level", ("level", gesture))
