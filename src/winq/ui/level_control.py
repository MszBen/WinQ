"""A dB level control (slider + spin box) and a stereo peak meter."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSlider, QWidget

from winq.audio.dsp import gain_to_db
from winq.model.cue import MIN_LEVEL_DB
from winq.ui import tokens

STEPS_PER_DB = 2  # slider resolution: 0.5 dB


class LevelControl(QWidget):
    """Emits `level_changed(db, gesture)`. `gesture` stays the same for one continuous
    slider drag, and changes for every other edit. Undo uses it to merge a drag into
    one step. At the bottom of the range the level is "-inf" (silent), reported as
    MIN_LEVEL_DB.

    The slider never takes keyboard focus: arrow keys must keep moving the cue
    selection during a show. Double-click the slider to reset it to 0 dB.
    """

    level_changed = Signal(float, int)

    def __init__(
        self, max_db: float, min_db: float = MIN_LEVEL_DB, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.min_db = min_db
        self.max_db = max_db
        self._gesture = 0

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(min_db * STEPS_PER_DB), round(max_db * STEPS_PER_DB))
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.setToolTip("Drag to change, double-click for 0 dB")
        self.slider.sliderPressed.connect(self._new_gesture)
        self.slider.valueChanged.connect(self._slider_moved)
        self.slider.installEventFilter(self)

        self.spin = QDoubleSpinBox()
        self.spin.setRange(min_db, max_db)
        self.spin.setSingleStep(0.5)
        self.spin.setDecimals(1)
        self.spin.setSuffix(" dB")
        self.spin.setSpecialValueText("-inf")
        self.spin.setKeyboardTracking(False)  # emit on Enter/focus-out, not per keystroke
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin.setFixedWidth(92)
        self.spin.valueChanged.connect(self._spin_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)

    def level(self) -> float:
        return self.spin.value()

    def set_level(self, db: float) -> None:
        """Show `db` without emitting anything."""
        db = self.min_db if math.isinf(db) else max(self.min_db, min(self.max_db, db))
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.slider.setValue(round(db * STEPS_PER_DB))
        self.spin.setValue(db)
        for w in (self.slider, self.spin):
            w.blockSignals(False)

    # internals ---------------------------------------------------------
    def _new_gesture(self) -> None:
        self._gesture += 1

    def _slider_moved(self, value: int) -> None:
        if not self.slider.isSliderDown():  # wheel / page clicks: each is its own step
            self._new_gesture()
        db = value / STEPS_PER_DB
        self.spin.blockSignals(True)
        self.spin.setValue(db)
        self.spin.blockSignals(False)
        self.level_changed.emit(db, self._gesture)

    def _spin_changed(self, db: float) -> None:
        self._new_gesture()
        self.slider.blockSignals(True)
        self.slider.setValue(round(db * STEPS_PER_DB))
        self.slider.blockSignals(False)
        self.level_changed.emit(db, self._gesture)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.slider and event.type() == QEvent.Type.MouseButtonDblClick:
            self.spin.setValue(0.0)  # goes through _spin_changed → emits
            return True
        return False


class PeakMeter(QWidget):
    """Stereo peak meter: fast attack, slow fall, a 1.5 s peak-hold line and a latching
    clip light (click to reset)."""

    FLOOR_DB = -60.0
    FALL_DB_PER_S = 24.0
    HOLD_S = 1.5
    CLIP_GAIN = 0.999

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 64)
        self.setToolTip("Output level (click to reset the clip light)")
        self._level = [self.FLOOR_DB, self.FLOOR_DB]
        self._hold = [self.FLOOR_DB, self.FLOOR_DB]
        self._hold_age = [0.0, 0.0]
        self.clipped = False

    def push(self, peaks: tuple[float, float], dt: float) -> None:
        for ch, gain in enumerate(peaks):
            db = max(self.FLOOR_DB, gain_to_db(gain))
            self._level[ch] = max(db, self._level[ch] - self.FALL_DB_PER_S * dt)
            self._hold_age[ch] += dt
            if db >= self._hold[ch] or self._hold_age[ch] > self.HOLD_S:
                self._hold[ch] = db
                self._hold_age[ch] = 0.0
            if gain >= self.CLIP_GAIN:
                self.clipped = True
        self.update()

    def levels(self) -> tuple[float, float]:
        return self._level[0], self._level[1]

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        self.clipped = False
        self.update()

    def _frac(self, db: float) -> float:
        return (db - self.FLOOR_DB) / -self.FLOOR_DB

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        w, h = self.width(), self.height()
        clip_h = 6
        bar_top = clip_h + 2
        bar_h = h - bar_top
        bar_w = (w - 6) / 2

        grad = QLinearGradient(0, h, 0, bar_top)
        grad.setColorAt(0.0, QColor(tokens.LAMP))
        grad.setColorAt(self._frac(-12), QColor(tokens.LAMP))
        grad.setColorAt(self._frac(-6), QColor(tokens.AMBER))
        grad.setColorAt(1.0, QColor(tokens.STOP))

        p.fillRect(0, 0, w, clip_h, QColor(tokens.STOP) if self.clipped else QColor(tokens.LINE))
        for ch in range(2):
            x = 2 + ch * (bar_w + 2)
            p.fillRect(QRectF(x, bar_top, bar_w, bar_h), QColor(tokens.BOOTH_0))
            lvl_h = bar_h * self._frac(self._level[ch])
            p.fillRect(QRectF(x, bar_top + bar_h - lvl_h, bar_w, lvl_h), grad)
            if self._hold[ch] > self.FLOOR_DB:
                y = bar_top + bar_h - bar_h * self._frac(self._hold[ch])
                p.fillRect(QRectF(x, y, bar_w, 2), QColor(tokens.TEXT))
