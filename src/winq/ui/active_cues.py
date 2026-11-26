"""Active Cues panel: every running instance in start order, which is the Escape order."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from winq.audio.cue_player import Progress
from winq.control.transport import RunningInstance
from winq.ui.cue_table import format_duration
from winq.ui.elided_label import ElidedLabel
from winq.ui.tokens import numeric_font


def format_remaining(p: Progress | None, r: RunningInstance, mode: str = "remaining") -> str:
    """Time readout for an Active Cues row: "-0:12.3" left (∞ while looping forever),
    or "+0:04.1" since it started when `mode` is "elapsed"."""
    if r.pending:
        return "cancelling" if r.stopping else "waiting"
    if r.stopping:
        return "stopping"
    if p is None:
        return ""
    if mode == "elapsed":
        return "+" + format_duration(p.elapsed)
    if p.remaining is None:
        return "∞"
    return "-" + format_duration(p.remaining)


class _Row(QFrame):
    def __init__(self, order: int, r: RunningInstance, on_stop: Callable[[int], None]) -> None:
        super().__init__()
        self.setObjectName("activeRow")
        self.voice_id = r.voice_id

        cue = r.cue
        label = f"{cue.number} {cue.name}" if cue.number else cue.name
        self.order = QLabel("·" if r.pending else f"{order}.")
        self.order.setObjectName("activeOrder")
        self.name = ElidedLabel(label)
        self.name.setObjectName("activeName")
        self.time = QLabel()
        self.time.setObjectName("activeTime")
        self.time.setFont(numeric_font(self.time.font()))
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        stop = QToolButton()
        stop.setText("■")
        stop.setObjectName("activeStop")
        stop.setToolTip("Stop this instance (uses its Stop fade)")
        stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        stop.clicked.connect(lambda: on_stop(self.voice_id))

        top = QHBoxLayout()
        top.addWidget(self.order)
        top.addWidget(self.name, 1)
        top.addWidget(self.time)
        top.addWidget(stop)
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 6, 8, 6)
        col.setSpacing(4)
        col.addLayout(top)
        col.addWidget(self.bar)

    def update_from(self, r: RunningInstance, p: Progress | None, mode: str) -> None:
        self.time.setText(format_remaining(p, r, mode))
        self.bar.setValue(int((p.pass_fraction if p else 0.0) * 1000))
        if r.pending:
            state = "waiting"
        else:
            state = "stopping" if r.stopping else "looping" if r.looping else "playing"
        if self.property("state") != state:
            self.setProperty("state", state)
            self.style().unpolish(self)
            self.style().polish(self)


class ActiveCuesPanel(QFrame):
    stop_requested = Signal(int)  # voice id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("activePanel")
        title = QLabel("Active cues, in Esc order")
        # Styled like the cue list's column header, with the same line under it.
        title.setObjectName("activeTitle")
        self.title = title
        self._empty = QLabel("Nothing playing")
        self._empty.setObjectName("panelEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._rows_host = QWidget()
        self._rows = QVBoxLayout(self._rows_host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(4)
        self._rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._rows_host)
        scroll.hide()  # shown only while something is running
        self._scroll = scroll

        body = QVBoxLayout()
        body.setContentsMargins(8, 8, 8, 8)
        body.addWidget(self._empty, 1)  # fills the panel, so the text sits in its centre
        body.addWidget(scroll, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # the title's line runs edge to edge
        layout.setSpacing(0)
        layout.addWidget(title)
        layout.addLayout(body, 1)
        self.time_mode = "remaining"  # or "elapsed" (a preference)
        self._row_widgets: list[_Row] = []
        self._ids: tuple[tuple[int, bool], ...] = ()

    def refresh(
        self,
        running: tuple[RunningInstance, ...],
        progress: Callable[[int], Progress | None],
    ) -> None:
        ids = tuple((r.voice_id, r.pending) for r in running)
        if ids != self._ids:
            self._rebuild(running)
        for row, r in zip(self._row_widgets, running, strict=True):
            row.update_from(r, progress(r.voice_id), self.time_mode)

    def _rebuild(self, running: tuple[RunningInstance, ...]) -> None:
        for w in self._row_widgets:
            w.hide()  # gone from view now, even before Qt deletes it
            self._rows.removeWidget(w)
            w.deleteLater()
        self._row_widgets = []
        for i, r in enumerate(running, start=1):
            row = _Row(i, r, self.stop_requested.emit)
            self._rows.insertWidget(self._rows.count() - 1, row)
            self._row_widgets.append(row)
        self._ids = tuple((r.voice_id, r.pending) for r in running)
        self._empty.setVisible(not running)
        self._scroll.setVisible(bool(running))
