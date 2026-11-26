"""A QLabel that shortens its text with "…" instead of forcing the window wider.

A plain QLabel's minimum width is its whole text, so one long cue name or file path
could make the main window wider than a laptop screen. This label can shrink to a few
characters; the full text is in its tooltip and returned by `text()`.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

MIN_CHARS = 6


class ElidedLabel(QLabel):
    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ) -> None:
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def text(self) -> str:  # type: ignore[override]
        return self._full

    def setText(self, text: str) -> None:  # type: ignore[override]
        self._full = text
        self.setToolTip(text if text else "")
        self._refresh()

    def clear(self) -> None:
        self.setText("")

    def minimumSizeHint(self) -> QSize:
        height = super().minimumSizeHint().height()
        return QSize(self.fontMetrics().averageCharWidth() * MIN_CHARS, height)

    def sizeHint(self) -> QSize:
        m = self.contentsMargins()
        width = self.fontMetrics().horizontalAdvance(self._full) + m.left() + m.right() + 2
        return QSize(width, super().sizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event) -> None:  # noqa: ANN001
        super().changeEvent(event)
        self._refresh()  # font/style changes alter the text width

    def _refresh(self) -> None:
        if not hasattr(self, "_mode"):  # change events can arrive during __init__
            return
        width = max(0, self.contentsRect().width())
        shown = self.fontMetrics().elidedText(self._full, self._mode, width)
        if super().text() != shown:
            super().setText(shown)
