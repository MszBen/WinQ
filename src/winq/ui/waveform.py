"""Waveform editor: trim (Start/End), fade-in / end-fade handles, the blue repeat-section
lines (Loop start / Loop end) with their loop-fade handles (blue squares at the bottom),
playheads and the preview point.

All positions are in seconds. The widget never edits a cue itself. It emits
`edit(attr, value, gesture)` while a handle is dragged. `gesture` stays the same for one
drag, so the whole drag undoes in one step.

Mouse:
- drag a Start/End line, a Loop start/end line, a fade handle (the squares at the top)
  or a loop-fade handle (the blue squares at the bottom)
- click elsewhere to set the preview point; drag elsewhere to pan
- Ctrl+wheel zooms around the cursor; wheel pans; double-click fits the whole file
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from winq.model.cue import LOOP_FADE_CROSSFADE, LOOP_FADE_JUMP, LOOP_FADE_PASS
from winq.ui import tokens
from winq.ui.tokens import numeric_font

MIN_REGION_S = 0.01  # Start and End can't get closer than this
MIN_VIEW_S = 0.05  # maximum zoom
HANDLE_PX = 6  # grab distance
# Lines that can sit on top of each other: (outer trim line, loop line, direction that
# means the loop line). Dragging from a shared spot picks the line by drag direction.
STACKED = (("start", "loop_start", +1), ("end", "loop_end", -1))
SNAP_END_PX = 4  # End within this of the file end snaps to "end of file" (None)
RULER_PX = 16

COL_BG = QColor(tokens.BOOTH_0)
COL_WAVE = QColor(tokens.WAVE)
COL_WAVE_OUT = QColor(tokens.WAVE_OUT)
COL_DIM = QColor(0, 0, 0, 110)
COL_START = QColor(tokens.LAMP)
COL_END = QColor(tokens.STOP)
COL_FADE = QColor(tokens.AMBER)
COL_PLAYHEAD = QColor("#ffffff")
COL_PREVIEW = QColor(tokens.CYAN)
COL_LOOP = QColor(tokens.LOOP)
COL_LOOP_BAND = QColor(COL_LOOP.red(), COL_LOOP.green(), COL_LOOP.blue(), 34)
COL_RULER = QColor(tokens.TEXT_DIM)


def nice_step(span: float, max_ticks: int) -> float:
    """A 1/2/5 × 10^n tick spacing giving at most `max_ticks` ticks over `span`."""
    raw = span / max(1, max_ticks)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def format_tick(t: float, step: float) -> str:
    minutes, secs = divmod(t, 60)
    decimals = 0 if step >= 1 else 1 if step >= 0.1 else 2
    return f"{int(minutes)}:{secs:0{3 + decimals if decimals else 2}.{decimals}f}"


class WaveformView(QWidget):
    edit = Signal(str, object, int)  # attribute, new value (float | None), gesture id
    preview_from_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wave_cache: tuple[tuple, QPixmap] | None = None
        self.setMinimumHeight(120)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._peaks: np.ndarray | None = None
        self._bucket_s = 1.0
        self.duration = 0.0
        self.view_start = 0.0
        self.view_end = 1.0
        self.start = 0.0
        self.end: float | None = None
        self.fade_in = 0.0
        self.end_fade = 0.0
        self.preview_from = 0.0
        self.loop_on = False  # repeat section shown and editable
        self.loop_start = 0.0
        self.loop_end: float | None = None  # None = the cue's End
        self.loop_outro = False  # after the last repeat it plays on to End
        self.loop_first_full = True  # the first play runs to End before the section
        self.loop_fades_on = False  # loop-fade handles shown and editable
        self.loop_fade_in = 0.0
        self.loop_fade_out = 0.0
        self.loop_fade_mode = LOOP_FADE_JUMP
        self._playheads: list[float] = []
        self._preview_head: float | None = None
        self._message = "No cue selected"
        self._drag: str | None = None
        self._stacked: tuple[str, str, int] | None = None
        self._gesture = 0
        self._press_x = 0.0
        self._pan_origin = (0.0, 0.0)

    # ----------------------------------------------------------------- data in
    def set_audio(self, peaks: np.ndarray, bucket_seconds: float, duration: float) -> None:
        new_file = self._peaks is None or duration != self.duration
        self._peaks = peaks
        self._wave_cache = None
        self._bucket_s = bucket_seconds
        self.duration = duration
        if new_file:
            self.fit()
        self.update()

    def clear(self, message: str) -> None:
        self._peaks = None
        self._wave_cache = None
        self.duration = 0.0
        self._message = message
        self._playheads = []
        self._preview_head = None
        self.update()

    @property
    def has_audio(self) -> bool:
        return self._peaks is not None

    def set_markers(self, start: float, end: float | None, fade_in: float, end_fade: float) -> None:
        if self._drag in ("start", "end", "fade_in", "end_fade"):
            return  # don't fight the user's own drag
        self.start, self.end, self.fade_in, self.end_fade = start, end, fade_in, end_fade
        self.preview_from = min(max(self.preview_from, start), self.end_eff)
        self.update()

    def set_section(
        self, on: bool, start: float, end: float | None, outro: bool, first_full: bool = True
    ) -> None:
        if self._drag in ("loop_start", "loop_end"):
            return
        self.loop_on, self.loop_start, self.loop_end, self.loop_outro = on, start, end, outro
        self.loop_first_full = first_full
        self.update()

    def set_loop_fades(self, on: bool, fade_in: float, fade_out: float, mode: str) -> None:
        if self._drag in ("loop_fade_in", "loop_fade_out"):
            return
        self.loop_fades_on, self.loop_fade_in, self.loop_fade_out = on, fade_in, fade_out
        self.loop_fade_mode = mode
        self.update()

    @property
    def loop_region(self) -> tuple[float, float]:
        """Where repeats jump between: the section, or the whole trim without one."""
        if self.loop_on:
            return self.loop_start_eff, self.loop_end_eff
        return self.start, self.end_eff

    @property
    def crossfade(self) -> bool:
        return self.loop_fade_mode == LOOP_FADE_CROSSFADE

    def loop_fade_max(self, attr: str) -> float:
        """Longest a loop fade can be: the loop region (a crossfade also needs as much
        audio before Loop start)."""
        lo, hi = self.loop_region
        if attr == "loop_fade_out" and self.crossfade:
            return max(0.0, min(hi - lo, lo))
        return max(0.0, hi - lo)

    @property
    def loop_start_eff(self) -> float:
        return min(max(self.loop_start, self.start), self.end_eff)

    @property
    def loop_end_eff(self) -> float:
        end = self.end_eff if self.loop_end is None else min(self.loop_end, self.end_eff)
        return max(end, self.loop_start_eff)

    @property
    def fade_end(self) -> float:
        """Where the end fade finishes: Loop end when the cue stops there, else End."""
        if self.loop_on and not self.loop_outro:
            return self.loop_end_eff
        return self.end_eff

    def set_playheads(self, positions: list[float], preview: float | None) -> None:
        if positions != self._playheads or preview != self._preview_head:
            self._playheads = positions
            self._preview_head = preview
            self.update()

    def set_preview_from(self, t: float) -> None:
        self.preview_from = t
        self.update()

    @property
    def end_eff(self) -> float:
        return self.duration if self.end is None else min(self.end, self.duration)

    # ----------------------------------------------------------------- view
    def fit(self) -> None:
        self.view_start, self.view_end = 0.0, max(self.duration, MIN_VIEW_S)
        self.update()

    def zoom(self, factor: float, around: float) -> None:
        """factor < 1 zooms in. `around` (seconds) stays under the same pixel."""
        span = self.view_end - self.view_start
        new_span = min(max(span * factor, MIN_VIEW_S), max(self.duration, MIN_VIEW_S))
        frac = (around - self.view_start) / span if span else 0.5
        self._set_view(around - frac * new_span, new_span)

    def pan(self, seconds: float) -> None:
        self._set_view(self.view_start + seconds, self.view_end - self.view_start)

    def _set_view(self, start: float, span: float) -> None:
        start = min(max(0.0, start), max(0.0, self.duration - span))
        self.view_start, self.view_end = start, start + span
        self.update()

    def x_of(self, t: float) -> float:
        span = self.view_end - self.view_start
        return (t - self.view_start) / span * self.width() if span else 0.0

    def t_of(self, x: float) -> float:
        return self.view_start + x / max(1, self.width()) * (self.view_end - self.view_start)

    # ----------------------------------------------------------------- hit testing
    def _wave_rect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height() - RULER_PX)

    def handle_at(self, x: float, y: float) -> str | None:
        top = 0 <= y <= 2 * HANDLE_PX + 4
        wave_h = self._wave_rect().height()
        bottom = wave_h - 2 * HANDLE_PX - 4 <= y <= wave_h
        candidates = []
        if bottom and self.loop_fades_on:
            lo, hi = self.loop_region
            candidates.append(("loop_fade_out", self.x_of(hi - self.loop_fade_out)))
            if not self.crossfade:
                candidates.append(("loop_fade_in", self.x_of(lo + self.loop_fade_in)))
        if top:
            candidates += [
                ("fade_in", self.x_of(self.start + self.fade_in)),
                ("end_fade", self.x_of(self.fade_end - self.end_fade)),
            ]
        candidates += [("start", self.x_of(self.start)), ("end", self.x_of(self.end_eff))]
        if self.loop_on:
            candidates += [
                ("loop_start", self.x_of(self.loop_start_eff)),
                ("loop_end", self.x_of(self.loop_end_eff)),
            ]
        best = min(candidates, key=lambda c: abs(c[1] - x))
        return best[0] if abs(best[1] - x) <= HANDLE_PX else None

    def _stacked_pair(self, handle: str | None) -> tuple[str, str, int] | None:
        """If `handle` is a trim or loop line lying on its partner, the pair."""
        if not self.loop_on or handle is None:
            return None
        pos = {
            "start": self.x_of(self.start),
            "end": self.x_of(self.end_eff),
            "loop_start": self.x_of(self.loop_start_eff),
            "loop_end": self.x_of(self.loop_end_eff),
        }
        for outer, loop, direction in STACKED:
            if handle in (outer, loop) and abs(pos[outer] - pos[loop]) <= 1.0:
                return outer, loop, direction
        return None

    # ----------------------------------------------------------------- mouse
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.has_audio or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        handle = self.handle_at(pos.x(), pos.y())
        self._stacked = self._stacked_pair(handle)
        self._drag = "stacked" if self._stacked else handle or "click"
        if self._drag != "click":
            self._gesture += 1
        self._press_x = pos.x()
        self._pan_origin = (self.view_start, self.view_end)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.has_audio:
            return
        x, y = event.position().x(), event.position().y()
        if self._drag is None:
            cursor = (
                Qt.CursorShape.SizeHorCursor if self.handle_at(x, y) else Qt.CursorShape.IBeamCursor
            )
            self.setCursor(cursor)
            return
        if self._drag == "click" and abs(x - self._press_x) > 4:
            self._drag = "pan"
        if self._drag == "stacked":
            if x == self._press_x:
                return
            outer, loop, direction = self._stacked
            self._drag = loop if (x - self._press_x) * direction > 0 else outer
        if self._drag == "pan":
            v0, v1 = self._pan_origin
            dt = (x - self._press_x) / max(1, self.width()) * (v1 - v0)
            self._set_view(v0 - dt, v1 - v0)
            return
        if self._drag != "click":
            self._drag_handle(self._drag, x)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag == "click":
            t = min(max(self.t_of(event.position().x()), self.start), self.end_eff - MIN_REGION_S)
            self.preview_from = max(self.start, t)
            self.preview_from_changed.emit(self.preview_from)
            self.update()
        self._drag = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.fit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self.has_audio:
            return
        steps = event.angleDelta().y() / 120 or event.angleDelta().x() / 120
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(0.8**steps, self.t_of(event.position().x()))
        else:
            self.pan(-steps * 0.1 * (self.view_end - self.view_start))
        event.accept()

    def _drag_handle(self, which: str, x: float) -> None:
        t = min(max(self.t_of(x), 0.0), self.duration)
        region = self.end_eff - self.start
        if which == "start":
            value = min(t, self.end_eff - MIN_REGION_S)
            self.start = round(max(0.0, value), 3)
            self.edit.emit("start_time", self.start, self._gesture)
        elif which == "end":
            if self.x_of(self.duration) - x <= SNAP_END_PX:
                self.end = None
            else:
                self.end = round(max(t, self.start + MIN_REGION_S), 3)
            self.edit.emit("end_time", self.end, self._gesture)
        elif which == "fade_in":
            self.fade_in = round(min(max(t - self.start, 0.0), region), 3)
            self.edit.emit("fade_in", self.fade_in, self._gesture)
        elif which == "end_fade":
            fade_region = self.fade_end - self.start
            self.end_fade = round(min(max(self.fade_end - t, 0.0), fade_region), 3)
            self.edit.emit("end_fade", self.end_fade, self._gesture)
        elif which == "loop_start":
            value = min(max(t, self.start), self.loop_end_eff - MIN_REGION_S)
            self.loop_start = round(max(0.0, value), 3)
            self.edit.emit("loop_start", self.loop_start, self._gesture)
        elif which == "loop_end":
            if self.x_of(self.end_eff) - x <= SNAP_END_PX:
                self.loop_end = None  # follows the cue's End
            else:
                value = min(max(t, self.loop_start_eff + MIN_REGION_S), self.end_eff)
                self.loop_end = round(value, 3)
            self.edit.emit("loop_end", self.loop_end, self._gesture)
        elif which == "loop_fade_in":
            lo, _hi = self.loop_region
            value = min(max(t - lo, 0.0), self.loop_fade_max(which))
            self.loop_fade_in = round(value, 3)
            self.edit.emit("loop_fade_in", self.loop_fade_in, self._gesture)
        elif which == "loop_fade_out":
            _lo, hi = self.loop_region
            value = min(max(hi - t, 0.0), self.loop_fade_max(which))
            self.loop_fade_out = round(value, 3)
            self.edit.emit("loop_fade_out", self.loop_fade_out, self._gesture)
        self.update()

    # ----------------------------------------------------------------- painting
    def _columns(self, width: int) -> tuple[np.ndarray, np.ndarray]:
        """Per-pixel (min, max) of the visible part of the waveform."""
        assert self._peaks is not None
        n = len(self._peaks)
        edges = self.view_start + np.arange(width + 1) * (self.view_end - self.view_start) / width
        idx = np.clip((edges / self._bucket_s).astype(np.int64), 0, max(0, n - 1))
        starts = idx[:-1]
        mins = np.minimum.reduceat(self._peaks[:, 0], starts)
        maxs = np.maximum.reduceat(self._peaks[:, 1], starts)
        # reduceat on an index that isn't smaller than the next one returns a single
        # element. That's right when zoomed in (several pixels per bucket).
        return mins, maxs

    def _wave_layer(self, w: int, h: float) -> QPixmap:
        """The waveform columns as a cached pixmap. Playheads repaint the widget ~30
        times a second; the waveform itself only changes on zoom, pan, trim or resize."""
        dpr = self.devicePixelRatioF()
        key = (w, h, dpr, self.view_start, self.view_end, self.start, self.end_eff,
               self.duration, id(self._peaks))  # fmt: skip
        if self._wave_cache is not None and self._wave_cache[0] == key:
            return self._wave_cache[1]
        pixmap = QPixmap(max(1, round(w * dpr)), max(1, round(h * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        mid = h / 2
        half = h / 2 * 0.92
        mins, maxs = self._columns(w)
        xs = np.arange(w)
        pix_t = self.view_start + (xs + 0.5) * (self.view_end - self.view_start) / w
        in_file = pix_t <= self.duration
        inside = (pix_t >= self.start) & (pix_t <= self.end_eff) & in_file
        top = (mid - maxs * half).tolist()
        bottom = (mid - mins * half).tolist()
        painter = QPainter(pixmap)
        for color, mask in ((COL_WAVE, inside), (COL_WAVE_OUT, ~inside & in_file)):
            painter.setPen(QPen(color, 1))
            painter.drawLines(
                [QLineF(x, top[x], x, bottom[x]) for x in np.flatnonzero(mask).tolist()]
            )
        painter.end()
        self._wave_cache = (key, pixmap)
        return pixmap

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.fillRect(self.rect(), COL_BG)
        rect = self._wave_rect()
        if not self.has_audio or self.width() < 2:
            p.setPen(COL_RULER)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._message)
            return

        w = self.width()
        p.drawPixmap(0, 0, self._wave_layer(w, rect.height()))

        # Dim outside the trim region.
        xs_ = self.x_of(self.start)
        xe = self.x_of(self.end_eff)
        p.fillRect(QRectF(0, 0, max(0.0, xs_), rect.height()), COL_DIM)
        p.fillRect(QRectF(xe, 0, max(0.0, w - xe), rect.height()), COL_DIM)

        if self.loop_on:
            ls, le = self.x_of(self.loop_start_eff), self.x_of(self.loop_end_eff)
            p.fillRect(QRectF(ls, 0, max(0.0, le - ls), rect.height()), COL_LOOP_BAND)
        self._paint_envelope(p, rect)
        if self.loop_fades_on:
            self._paint_loop_fades(p, rect)
        self._paint_markers(p, rect)
        self._paint_ruler(p)

    def _paint_envelope(self, p: QPainter, rect: QRectF) -> None:
        """Gain envelope (fade-in, flat, end fade) drawn as a line from top (full) down."""
        top, bottom = 2.0, rect.height() - 2
        path = QPainterPath()
        steps = 24
        pts: list[tuple[float, float]] = []
        s, e = self.start, self.fade_end
        if self.fade_in > 0:
            for i in range(steps + 1):
                f = i / steps
                pts.append((s + f * self.fade_in, 0.5 - 0.5 * math.cos(math.pi * f)))
        else:
            pts.append((s, 1.0))
        if self.end_fade > 0:
            for i in range(steps + 1):
                f = i / steps
                pts.append(
                    (e - self.end_fade + f * self.end_fade, 0.5 + 0.5 * math.cos(math.pi * f))
                )
        else:
            pts.append((e, 1.0))
        for i, (t, g) in enumerate(pts):
            pt = QPointF(self.x_of(t), bottom - g * (bottom - top))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(COL_FADE, 1.5))
        p.drawPath(path)
        p.setBrush(COL_FADE)
        for t in (s + self.fade_in, e - self.end_fade):
            p.drawRect(QRectF(self.x_of(t) - HANDLE_PX / 2 - 1, top, HANDLE_PX + 2, HANDLE_PX + 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_loop_fades(self, p: QPainter, rect: QRectF) -> None:
        """Loop-fade ramps in the loop colour (full level at the top, silence at the
        bottom), with their handles along the bottom edge."""
        top, bottom = 2.0, rect.height() - 2
        lo, hi = self.loop_region
        fi = min(self.loop_fade_in, self.loop_fade_max("loop_fade_in"))
        fo = min(self.loop_fade_out, self.loop_fade_max("loop_fade_out"))

        def ramp(t0: float, length: float, rising: bool, dashed: bool = False) -> None:
            if length <= 0:
                return
            path = QPainterPath()
            for i in range(25):
                f = i / 24
                g = 0.5 - 0.5 * math.cos(math.pi * f)
                pt = QPointF(self.x_of(t0 + f * length), bottom - (g if rising else 1 - g)
                             * (bottom - top))  # fmt: skip
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
            p.setPen(QPen(COL_LOOP, 1.5, style))
            p.drawPath(path)

        handles = [hi - fo]
        if self.crossfade:
            ramp(hi - fo, fo, rising=False)
            ramp(lo - fo, fo, rising=True, dashed=True)  # the audio blended in at the jump
        else:
            handles.append(lo + fi)
            ramp(lo, fi, rising=True)
            ramp(hi - fo, fo, rising=False)
            jump_from_end = self.loop_on and self.loop_first_full and hi < self.end_eff
            if jump_from_end and self.loop_fade_mode in (LOOP_FADE_JUMP, LOOP_FADE_PASS):
                ramp(self.end_eff - fo, fo, rising=False)
            if self.loop_on and self.loop_outro and self.loop_fade_mode == LOOP_FADE_PASS:
                ramp(hi, min(fi, self.end_eff - hi), rising=True)
        p.setPen(QPen(COL_LOOP, 1.5))
        p.setBrush(COL_LOOP)
        size = HANDLE_PX + 2
        for t in handles:
            p.drawRect(QRectF(self.x_of(t) - size / 2, bottom - size, size, size))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_markers(self, p: QPainter, rect: QRectF) -> None:
        h = rect.height()
        font = numeric_font(self.font(), point_size=8.5)
        p.setFont(font)
        for t, color, label in ((self.start, COL_START, "S"), (self.end_eff, COL_END, "E")):
            x = self.x_of(t)
            p.setPen(QPen(color, 2))
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            p.drawText(QPointF(x + 3 if label == "S" else x - 10, h - 4), label)
        if self.loop_on:
            p.setPen(QPen(COL_LOOP, 2))
            for t in (self.loop_start_eff, self.loop_end_eff):
                x = self.x_of(t)
                p.drawLine(QPointF(x, 0), QPointF(x, h))
            # Label inside the band, below the fade handles.
            p.drawText(QPointF(self.x_of(self.loop_start_eff) + 4, 2 * HANDLE_PX + 16), "loop")
        x = self.x_of(self.preview_from)
        p.setPen(QPen(COL_PREVIEW, 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x, 0), QPointF(x, h))
        p.setPen(QPen(COL_PLAYHEAD, 1.5))
        for t in self._playheads:
            x = self.x_of(t)
            p.drawLine(QPointF(x, 0), QPointF(x, h))
        if self._preview_head is not None:
            x = self.x_of(self._preview_head)
            p.setPen(QPen(COL_PREVIEW, 2))
            p.drawLine(QPointF(x, 0), QPointF(x, h))

    def _paint_ruler(self, p: QPainter) -> None:
        y0 = self.height() - RULER_PX
        span = self.view_end - self.view_start
        step = nice_step(span, max(2, self.width() // 90))
        p.setPen(COL_RULER)
        t = math.ceil(self.view_start / step) * step
        while t <= self.view_end + 1e-9:
            x = self.x_of(t)
            p.drawLine(QPointF(x, y0), QPointF(x, y0 + 4))
            p.drawText(QPointF(x + 2, self.height() - 3), format_tick(t, step))
            t += step
