"""Undoable edits to the cue list. Every user edit goes through one of these."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QUndoCommand

from winq.model.cue import AudioCue

if TYPE_CHECKING:
    from winq.ui.cue_table import CueTableModel


class AddCuesCommand(QUndoCommand):
    def __init__(self, model: CueTableModel, row: int, cues: list[AudioCue]) -> None:
        n = len(cues)
        super().__init__(f"Add {n} cue{'s' if n != 1 else ''}")
        self._model = model
        self._row = max(0, min(row, model.rowCount()))
        self._cues = list(cues)

    def redo(self) -> None:
        self._model.insert_cues(self._row, self._cues)

    def undo(self) -> None:
        self._model.remove_cues(self._row, len(self._cues))


class RemoveCueCommand(QUndoCommand):
    def __init__(self, model: CueTableModel, row: int) -> None:
        super().__init__("Delete cue")
        self._model = model
        self._row = row
        self._cues: list[AudioCue] = []

    def redo(self) -> None:
        self._cues = self._model.remove_cues(self._row, 1)

    def undo(self) -> None:
        self._model.insert_cues(self._row, self._cues)


class MoveCueCommand(QUndoCommand):
    def __init__(self, model: CueTableModel, src: int, final_index: int) -> None:
        super().__init__("Move cue")
        self._model = model
        self._src = src
        self._final = final_index

    def redo(self) -> None:
        self._model.move_cue(self._src, self._final)

    def undo(self) -> None:
        self._model.move_cue(self._final, self._src)


MERGE_ID = 1  # all mergeable commands share this id; mergeWith checks the details


class EditCueCommand(QUndoCommand):
    """Set one attribute of one cue. Cues are found by id, so row moves don't matter.

    Edits with the same non-None `merge_key` on the same cue/attribute collapse into one
    undo step, so a whole slider drag (or one typing session) undoes at once.
    """

    def __init__(
        self,
        model: CueTableModel,
        cue_id: str,
        attr: str,
        old: Any,
        new: Any,
        label: str,
        merge_key: object = None,
    ) -> None:
        super().__init__(f"Change {label}")
        self._model = model
        self._cue_id = cue_id
        self._attr = attr
        self._old = old
        self._new = new
        self._merge_key = merge_key

    def id(self) -> int:
        return MERGE_ID if self._merge_key is not None else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, EditCueCommand) or other._merge_key != self._merge_key:
            return False
        if (other._cue_id, other._attr) != (self._cue_id, self._attr):
            return False
        self._new = other._new
        return True

    def redo(self) -> None:
        self._model.set_cue_attr(self._cue_id, self._attr, self._new)

    def undo(self) -> None:
        self._model.set_cue_attr(self._cue_id, self._attr, self._old)


class SetMasterCommand(QUndoCommand):
    """Change the show's master level. Lives in the undo stack so that it marks the
    show as unsaved, and a whole fader drag undoes in one step."""

    def __init__(
        self, apply: Callable[[float], None], old: float, new: float, merge_key: object = None
    ) -> None:
        super().__init__("Change master level")
        self._apply = apply
        self._old = old
        self._new = new
        self._merge_key = merge_key

    def id(self) -> int:
        return MERGE_ID if self._merge_key is not None else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, SetMasterCommand) or other._merge_key != self._merge_key:
            return False
        self._new = other._new
        return True

    def redo(self) -> None:
        self._apply(self._new)

    def undo(self) -> None:
        self._apply(self._old)


class MarkerCommand(QUndoCommand):
    """Does nothing. Pushed to make the show "unsaved" (e.g. after recovering an
    autosave), so closing asks to save."""

    def redo(self) -> None:
        pass

    def undo(self) -> None:
        pass
