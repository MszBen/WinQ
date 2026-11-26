"""A seconds spin box that shows and accepts m:ss.cc (also plain seconds like 12.5)."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QWidget

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(\d*(?:\.\d*)?)\s*$")


def format_time(seconds: float) -> str:
    hundredths = round(seconds * 100)
    minutes, rest = divmod(hundredths, 6000)
    return f"{minutes}:{rest // 100:02d}.{rest % 100:02d}"


def parse_time(text: str) -> float | None:
    m = _TIME_RE.match(text)
    if not m or (m.group(1) is None and not m.group(2)):
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    secs = float(m.group(2)) if m.group(2) not in ("", ".") else 0.0
    return minutes * 60 + secs


class TimeEdit(QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDecimals(3)
        self.setSingleStep(0.1)
        self.setRange(0.0, 24 * 3600)
        self.setKeyboardTracking(False)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setFixedWidth(96)

    def textFromValue(self, value: float) -> str:
        return format_time(value)

    def valueFromText(self, text: str) -> float:
        v = parse_time(text)
        return self.value() if v is None else v

    def validate(self, text: str, pos: int) -> object:
        if parse_time(text) is not None:
            return QValidator.State.Acceptable, text, pos
        if _TIME_RE.match(text) or text.strip() in ("", ":"):
            return QValidator.State.Intermediate, text, pos
        return QValidator.State.Invalid, text, pos
