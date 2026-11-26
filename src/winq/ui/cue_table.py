"""Cue list table: the Qt model wrapping CueList, and the view with drag-and-drop.

The model's mutator methods (`insert_cues`, `remove_cues`, `move_cue`, `set_cue_attr`)
do NOT touch the undo stack. They are what undo commands call. User actions go through
commands: in-table edits push an EditCueCommand; drops emit signals that the main
window turns into commands.
"""

from __future__ import annotations

import functools
import math
from enum import IntEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QByteArray,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QUndoStack
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from winq.audio.loader import is_supported
from winq.model.cue import CONTINUE, FOLLOW, MIN_LEVEL_DB, AudioCue
from winq.model.cue_list import CueList
from winq.ui import tokens
from winq.ui.commands import EditCueCommand

ROW_MIME = "application/x-winq-cue-row"
MISSING_COLOR = QColor("#ff8a8d")  # stop red, lifted for text on navy
PLAY_STATE_ICONS = {"playing": "▶", "looping": "∞", "stopping": "◼", "waiting": "⧗"}
PLAY_STATE_COLORS = {
    "playing": QColor(tokens.LAMP),
    "looping": QColor(tokens.AMBER),
    "stopping": QColor(tokens.TEXT_MUTED),
    "waiting": QColor(tokens.CYAN),
}

Index = QModelIndex | QPersistentModelIndex


@functools.cache
def _numeric_font() -> QFont:
    return tokens.numeric_font()


class Col(IntEnum):
    STATUS = 0
    NUMBER = 1
    NAME = 2
    DURATION = 3
    LEVEL = 4
    REPEATS = 5
    THEN = 6
    FILE = 7


HEADERS = {
    Col.STATUS: "",
    Col.NUMBER: "#",
    Col.NAME: "Name",
    Col.DURATION: "Duration",
    Col.LEVEL: "Level",
    Col.REPEATS: "Repeats",
    Col.THEN: "Then",
    Col.FILE: "File",
}
NUMERIC_COLS = (Col.NUMBER, Col.DURATION, Col.LEVEL, Col.REPEATS)

EDITABLE = {Col.NUMBER: ("number", "number"), Col.NAME: ("name", "name")}


# --------------------------------------------------------------------- formatting
def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    tenths = round(seconds * 10)
    minutes, rest = divmod(tenths, 600)
    return f"{minutes}:{rest // 10:02d}.{rest % 10}"


def format_level(db: float) -> str:
    if db <= MIN_LEVEL_DB or math.isinf(db):
        return "-inf"
    if db == 0:
        return "0 dB"
    return f"{db:+.1f} dB".replace(".0 ", " ")


def format_then(mode: str, delay: float) -> str:
    """Short label for the "Then" column: what starts the next cue automatically."""
    extra = f" +{delay:g}s" if delay > 0 else ""
    if mode == FOLLOW:
        return f"↓ at end{extra}"
    if mode == CONTINUE:
        return f"↓ with{extra}"
    return ""


def format_repeats(repeats: int, is_infinite: bool, section: bool = False) -> str:
    """ "∞" or "↻ 2"; a trailing "§" means only a section of the cue repeats."""
    if is_infinite:
        text = "∞"
    elif repeats == 0:
        return ""
    else:
        text = f"↻ {repeats}"
    return f"{text} §" if section else text


# --------------------------------------------------------------------- model
class CueTableModel(QAbstractTableModel):
    files_dropped = Signal(list, int)  # (list[Path], insert row)
    move_requested = Signal(int, int)  # (src row, final row)
    cue_attr_changed = Signal(str, str)  # (cue id, attribute): after any edit, undo or redo

    def __init__(self, undo_stack: QUndoStack, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cue_list = CueList()
        self.undo_stack = undo_stack
        self._play_states: dict[str, str] = {}  # cue id → a PLAY_STATE_ICONS key
        self.editable = True  # False in Show mode: no edits, drags or drops

    def set_play_states(self, states: dict[str, str]) -> None:
        if states == self._play_states:
            return
        self._play_states = dict(states)
        if self.rowCount():
            last = self.rowCount() - 1
            self.dataChanged.emit(self.index(0, Col.STATUS), self.index(last, Col.STATUS))

    def play_state(self, cue_id: str) -> str | None:
        return self._play_states.get(cue_id)

    # read -------------------------------------------------------------
    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.cue_list)

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(Col)

    def cue_at(self, row: int) -> AudioCue:
        return self.cue_list[row]

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[Col(section)]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            # Headers line up with their column's text.
            col = Col(section)
            if col in (Col.DURATION, Col.LEVEL):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col in (Col.STATUS, Col.REPEATS):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        cue = self.cue_list[index.row()]
        col = Col(index.column())
        if col == Col.STATUS:
            state = self._play_states.get(cue.id)
            if state is None:
                return None
            if role == Qt.ItemDataRole.DisplayRole:
                return PLAY_STATE_ICONS[state]
            if role == Qt.ItemDataRole.ForegroundRole:
                return PLAY_STATE_COLORS[state]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
            if role == Qt.ItemDataRole.ToolTipRole:
                return state.capitalize()
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            match col:
                case Col.NUMBER:
                    return cue.number
                case Col.NAME:
                    return cue.name
                case Col.DURATION:
                    return format_duration(cue.pass_duration)
                case Col.LEVEL:
                    return format_level(cue.level_db)
                case Col.REPEATS:
                    return format_repeats(cue.repeats, cue.is_infinite, cue.loop_section)
                case Col.THEN:
                    return format_then(cue.continue_mode, cue.continue_delay)
                case Col.FILE:
                    return cue.file_path.name
            return None
        if role == Qt.ItemDataRole.FontRole and col in NUMERIC_COLS:
            return _numeric_font()
        if role == Qt.ItemDataRole.ForegroundRole and col == Col.FILE and cue.is_missing:
            return MISSING_COLOR
        if role == Qt.ItemDataRole.ToolTipRole and col == Col.FILE:
            prefix = "Missing or unreadable: " if cue.is_missing else ""
            return prefix + str(cue.file_path)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (Col.DURATION, Col.LEVEL):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col == Col.REPEATS:
                return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def flags(self, index: Index) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled if self.editable else Qt.ItemFlag.NoItemFlags
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not self.editable:
            return f
        f |= Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        if Col(index.column()) in EDITABLE:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    # in-table editing (undoable) ---------------------------------------
    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or not self.editable:
            return False
        spec = EDITABLE.get(Col(index.column()))
        if spec is None:
            return False
        attr, label = spec
        cue = self.cue_list[index.row()]
        new = str(value).strip()
        old = getattr(cue, attr)
        if new == old:
            return False
        self.undo_stack.push(EditCueCommand(self, cue.id, attr, old, new, label))
        return True

    # mutators used by undo commands -------------------------------------
    def insert_cues(self, row: int, cues: list[AudioCue]) -> None:
        if not cues:
            return
        row = max(0, min(row, len(self.cue_list)))
        self.beginInsertRows(QModelIndex(), row, row + len(cues) - 1)
        self.cue_list.insert(row, cues)
        self.endInsertRows()

    def remove_cues(self, row: int, count: int) -> list[AudioCue]:
        self.beginRemoveRows(QModelIndex(), row, row + count - 1)
        removed = self.cue_list.remove(row, count)
        self.endRemoveRows()
        return removed

    def move_cue(self, src: int, final_index: int) -> None:
        if src == final_index:
            return
        dest_child = final_index + 1 if final_index > src else final_index
        self.beginMoveRows(QModelIndex(), src, src, QModelIndex(), dest_child)
        self.cue_list.move(src, final_index)
        self.endMoveRows()

    def set_cue_attr(self, cue_id: str, attr: str, value: Any) -> None:
        row = self.cue_list.index_of(cue_id)
        if row is None:
            return
        setattr(self.cue_list[row], attr, value)
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(Col) - 1))
        self.cue_attr_changed.emit(cue_id, attr)

    def reset_cues(self, cues: list[AudioCue]) -> None:
        self.beginResetModel()
        self.cue_list.replace_all(cues)
        self.endResetModel()

    # drag and drop -----------------------------------------------------
    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [ROW_MIME, "text/uri-list"]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        mime = QMimeData()
        if indexes:
            mime.setData(ROW_MIME, QByteArray(str(indexes[0].row()).encode()))
        return mime

    def canDropMimeData(self, data: QMimeData, action, row, column, parent: Index) -> bool:  # noqa: ANN001
        return self.editable and (data.hasFormat(ROW_MIME) or data.hasUrls())

    def dropMimeData(self, data: QMimeData, action, row, column, parent: Index) -> bool:  # noqa: ANN001
        if not self.editable:
            return False
        if row == -1:
            row = parent.row() if parent.isValid() else self.rowCount()
        if data.hasFormat(ROW_MIME):
            src = int(bytes(data.data(ROW_MIME)).decode())
            final = row - 1 if row > src else row
            if final != src:
                self.move_requested.emit(src, final)
            return False  # we handle the move ourselves; Qt must not remove the source row
        if data.hasUrls():
            paths = [Path(u.toLocalFile()) for u in data.urls() if u.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths, row)
                return True
        return False


def supported_audio_files(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """Split dropped paths into (audio files we can use, everything else). Folders are
    expanded one level deep, sorted by name."""
    ok: list[Path] = []
    rejected: list[Path] = []
    for p in paths:
        candidates = sorted(c for c in p.iterdir() if c.is_file()) if p.is_dir() else [p]
        for c in candidates:
            (ok if is_supported(c) else rejected).append(c)
    return ok, rejected


# --------------------------------------------------------------------- view
class CueTableView(QTableView):
    EMPTY_HINT = "Drag audio files here, or use File → Add Audio Files…"
    FIXED_WIDTHS = {
        Col.STATUS: 28,
        Col.NUMBER: 52,
        Col.DURATION: 76,
        Col.LEVEL: 70,
        Col.REPEATS: 68,
        Col.THEN: 92,
    }
    MIN_NAME_WIDTH = 120
    MIN_FILE_WIDTH = 60
    NAME_SHARE = 0.62  # of the width left after the fixed columns; File gets the rest

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cueTable")
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(30)

    def setModel(self, model: CueTableModel) -> None:  # type: ignore[override]
        super().setModel(model)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setMinimumSectionSize(24)
        for col, width in self.FIXED_WIDTHS.items():
            header.resizeSection(col, width)
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        # Name and File share whatever width is left (see _fit_columns); never sideways scroll.
        header.setSectionResizeMode(Col.NAME, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(Col.FILE, QHeaderView.ResizeMode.Fixed)
        self._fit_columns()

    def _fit_columns(self) -> None:
        if self.model() is None:
            return
        fixed = sum(self.FIXED_WIDTHS.values())
        spare = max(0, self.viewport().width() - fixed)
        name = max(self.MIN_NAME_WIDTH, int(spare * self.NAME_SHARE))
        header = self.horizontalHeader()
        header.resizeSection(Col.NAME, name)
        header.resizeSection(Col.FILE, max(self.MIN_FILE_WIDTH, spare - name))

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._fit_columns()

    def minimumSizeHint(self) -> QSize:
        width = sum(self.FIXED_WIDTHS.values()) + self.MIN_NAME_WIDTH + self.MIN_FILE_WIDTH
        width += self.verticalScrollBar().sizeHint().width() + 2 * self.frameWidth()
        return QSize(width, super().minimumSizeHint().height())

    def selected_row(self) -> int | None:
        rows = self.selectionModel().selectedRows() if self.selectionModel() else []
        return rows[0].row() if rows else None

    def select_row(self, row: int) -> None:
        model = self.model()
        if model is None or model.rowCount() == 0:
            return
        row = max(0, min(row, model.rowCount() - 1))
        self.selectRow(row)
        self.scrollTo(model.index(row, 0))

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        model = self.model()
        if model is not None and model.rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(tokens.TEXT_DIM))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.EMPTY_HINT)
