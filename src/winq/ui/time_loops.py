"""Inspector "Time & Loops" tab: waveform with trim/fade handles, exact time fields,
repeats, Stop fade and the Preview button."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from winq.audio.loader import PEAK_BUCKET_FRAMES, LoadedAudio
from winq.model.cue import (
    AFTER_OUTRO,
    AFTER_STOP,
    CONTINUE,
    FIRST_FULL,
    FIRST_INTRO,
    FOLLOW,
    INFINITE_REPEATS,
    LOOP_FADE_CROSSFADE,
    LOOP_FADE_JUMP,
    LOOP_FADE_LOOP_END,
    LOOP_FADE_PASS,
    NO_CONTINUE,
    AudioCue,
)
from winq.ui.time_edit import TimeEdit, format_time
from winq.ui.waveform import MIN_REGION_S, WaveformView

END_SNAP_S = 0.0005  # an End this close to the file end means "end of file" (None)
THEN_CHOICES = (
    (NO_CONTINUE, "Nothing (wait for Space)"),
    (FOLLOW, "Start next cue when this one ends"),
    (CONTINUE, "Start next cue with this one"),
)
LOOP_FIRST_CHOICES = (
    (FIRST_FULL, "Whole track, then the section"),
    (FIRST_INTRO, "From Start into the section"),
)
LOOP_AFTER_CHOICES = (
    (AFTER_STOP, "Stop at Loop end"),
    (AFTER_OUTRO, "Play on to the End"),
)
LOOP_FADE_CHOICES = (
    (LOOP_FADE_JUMP, "Dip at every jump back"),
    (LOOP_FADE_LOOP_END, "Dip at Loop end only"),
    (LOOP_FADE_PASS, "Fade every pass"),
    (LOOP_FADE_CROSSFADE, "Crossfade (no dip)"),
)
LOOP_FADE_TIPS = {
    LOOP_FADE_JUMP: "Fade out before every jump back to Loop start, fade in after it",
    LOOP_FADE_LOOP_END: "Like 'every jump', but the jump from End after the whole first "
    "play only fades in",
    LOOP_FADE_PASS: "Every pass fades in and out, also into the outro and at the stop",
    LOOP_FADE_CROSSFADE: "Blend the end of each pass with the audio just before Loop "
    "start, so the loop is seamless. Needs that much audio before Loop start.",
}
WAVE_LABELS = {
    "start_time": "start",
    "end_time": "end",
    "fade_in": "fade in",
    "end_fade": "end fade",
    "loop_start": "loop start",
    "loop_end": "loop end",
    "loop_fade_in": "loop fade in",
    "loop_fade_out": "loop fade out",
}


def _seconds_box(maximum: float = 600.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0.0, maximum)
    box.setDecimals(2)
    box.setSingleStep(0.1)
    box.setSuffix(" s")
    box.setKeyboardTracking(False)
    box.setAlignment(Qt.AlignmentFlag.AlignRight)
    box.setFixedWidth(80)
    return box


class TimeLoopsTab(QWidget):
    edit = Signal(str, object, str, object)  # attr, value, undo label, merge key
    edit_group = Signal(list, str)  # [(attr, value), ...] applied as one undo step, label
    preview_requested = Signal(float)  # start previewing from this time
    preview_stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cue: AudioCue | None = None
        self._audio_duration: float | None = None
        self.loop_fades_whole = False  # preference: loop fades without a section too

        self.waveform = WaveformView()
        self.waveform.edit.connect(self._wave_edit)
        self.waveform.preview_from_changed.connect(self._preview_from_changed)

        self.start = TimeEdit()
        self.start.valueChanged.connect(self._start_typed)
        self.end = TimeEdit()
        self.end.setToolTip("At the file's length = play to the end of the file")
        self.end.valueChanged.connect(self._end_typed)
        self.length = QLabel()
        self.length.setObjectName("panelEmpty")
        self.fade_in = _seconds_box()
        self.fade_in.valueChanged.connect(lambda v: self._fade_typed("fade_in", v))
        self.end_fade = _seconds_box()
        self.end_fade.valueChanged.connect(lambda v: self._fade_typed("end_fade", v))

        self.repeats = QSpinBox()
        self.repeats.setRange(0, 999)
        self.repeats.setKeyboardTracking(False)
        self.repeats.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.repeats.setFixedWidth(60)
        self.repeats.setToolTip("Extra passes after the first (1 = plays twice)")
        self.repeats.valueChanged.connect(self._repeats_typed)
        self.loop_forever = QCheckBox("Loop forever")
        self.loop_forever.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.loop_forever.setToolTip("Loops until Space is pressed on this cue")
        self.loop_forever.toggled.connect(self._loop_toggled)
        self.loop_section = QCheckBox("Repeat only a section")
        self.loop_section.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.loop_section.setToolTip(
            "Repeats play only Loop start → Loop end (the blue lines on the waveform)"
        )
        self.loop_section.toggled.connect(self._section_toggled)
        self.loop_start = TimeEdit()
        self.loop_start.valueChanged.connect(self._loop_start_typed)
        self.loop_end = TimeEdit()
        self.loop_end.setToolTip("At the cue's End = the section runs to the End")
        self.loop_end.valueChanged.connect(self._loop_end_typed)
        self.loop_first = QComboBox()
        self.loop_first.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for mode, text in LOOP_FIRST_CHOICES:
            self.loop_first.addItem(text, mode)
        self.loop_first.setToolTip("What the first play covers before the section repeats")
        self.loop_first.currentIndexChanged.connect(
            lambda _i: self._emit("loop_first", self.loop_first.currentData(), "first play")
        )
        self.loop_after = QComboBox()
        self.loop_after.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for mode, text in LOOP_AFTER_CHOICES:
            self.loop_after.addItem(text, mode)
        self.loop_after.setToolTip(
            "When the last repeat (or the loop ended with Space) reaches Loop end"
        )
        self.loop_after.currentIndexChanged.connect(
            lambda _i: self._emit("loop_after", self.loop_after.currentData(), "after loop")
        )
        self._section_fields = (self.loop_start, self.loop_end, self.loop_first, self.loop_after)
        self.loop_fade_mode = QComboBox()
        self.loop_fade_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for i, (mode, text) in enumerate(LOOP_FADE_CHOICES):
            self.loop_fade_mode.addItem(text, mode)
            self.loop_fade_mode.setItemData(i, LOOP_FADE_TIPS[mode], Qt.ItemDataRole.ToolTipRole)
        self.loop_fade_mode.currentIndexChanged.connect(
            lambda _i: self._emit(
                "loop_fade_mode", self.loop_fade_mode.currentData(), "loop fade mode"
            )
        )
        self.loop_fade_in = _seconds_box()
        self.loop_fade_in.setToolTip("Fade up from Loop start after each jump back")
        self.loop_fade_in.valueChanged.connect(lambda v: self._loop_fade_typed("loop_fade_in", v))
        self.loop_fade_out = _seconds_box()
        self.loop_fade_out.valueChanged.connect(lambda v: self._loop_fade_typed("loop_fade_out", v))
        self.loop_fade_in_label = QLabel("Fade in")
        self.loop_fade_out_label = QLabel("Fade out")
        self._loop_fade_fields = (self.loop_fade_mode, self.loop_fade_in, self.loop_fade_out)

        self.stop_fade = _seconds_box(60.0)
        self.stop_fade.setToolTip("Fade-out time when Escape stops this cue")
        self.stop_fade.valueChanged.connect(
            lambda v: self._emit("stop_fade", round(v, 3), "stop fade")
        )

        self.then = QComboBox()
        self.then.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space stays GO
        for mode, text in THEN_CHOICES:
            self.then.addItem(text, mode)
        self.then.setToolTip(
            "Start the next cue automatically. Esc or Stop All on this cue cancels it."
        )
        self.then.currentIndexChanged.connect(self._then_changed)
        self.then_delay = _seconds_box(3600.0)
        self.then_delay.setToolTip(
            "Extra wait: after this cue ends (when it ends) or after it starts (with it)"
        )
        self.then_delay.valueChanged.connect(
            lambda v: self._emit("continue_delay", round(v, 3), "then delay")
        )

        self.preview = QPushButton("▶ Preview")
        self.preview.setObjectName("previewButton")
        self.preview.setCheckable(True)
        self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preview.setToolTip(
            "Play from the dashed marker (click the waveform to move it). "
            "Not part of GO/Esc; press again to stop."
        )
        self.preview.clicked.connect(self._preview_clicked)
        self.preview_from = QLabel()
        self.preview_from.setObjectName("panelEmpty")

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.addWidget(QLabel("Start"), 0, 0)
        grid.addWidget(self.start, 0, 1)
        grid.addWidget(QLabel("End"), 0, 2)
        grid.addWidget(self.end, 0, 3)
        grid.addWidget(self.length, 0, 4)
        grid.addWidget(QLabel("Fade in"), 0, 5)
        grid.addWidget(self.fade_in, 0, 6)
        grid.addWidget(QLabel("End fade"), 0, 7)
        grid.addWidget(self.end_fade, 0, 8)
        grid.addWidget(QLabel("Repeats"), 1, 0)
        grid.addWidget(self.repeats, 1, 1)
        grid.addWidget(self.loop_forever, 1, 2, 1, 2)
        grid.addWidget(QLabel("Stop fade (Esc)"), 1, 5)
        grid.addWidget(self.stop_fade, 1, 6)
        grid.addWidget(self.loop_section, 2, 0, 1, 2)
        grid.addWidget(QLabel("Loop start"), 2, 2)
        grid.addWidget(self.loop_start, 2, 3)
        grid.addWidget(QLabel("Loop end"), 2, 5)
        grid.addWidget(self.loop_end, 2, 6)
        grid.addWidget(QLabel("First play"), 3, 0)
        grid.addWidget(self.loop_first, 3, 1, 1, 3)
        grid.addWidget(QLabel("After last"), 3, 5)
        grid.addWidget(self.loop_after, 3, 6, 1, 3)
        grid.addWidget(QLabel("Loop fades"), 4, 0)
        grid.addWidget(self.loop_fade_mode, 4, 1, 1, 3)
        grid.addWidget(self.loop_fade_in_label, 4, 5)
        grid.addWidget(self.loop_fade_in, 4, 6)
        grid.addWidget(self.loop_fade_out_label, 4, 7)
        grid.addWidget(self.loop_fade_out, 4, 8)
        grid.addWidget(QLabel("Then"), 5, 0)
        grid.addWidget(self.then, 5, 1, 1, 3)
        grid.addWidget(QLabel("Delay"), 5, 5)
        grid.addWidget(self.then_delay, 5, 6)
        grid.setColumnStretch(9, 1)

        buttons = QHBoxLayout()
        buttons.addWidget(self.preview)
        buttons.addWidget(self.preview_from)
        buttons.addStretch(1)
        hint = QLabel("Ctrl+wheel: zoom · drag: pan · double-click: fit")
        hint.setObjectName("panelEmpty")
        buttons.addWidget(hint)

        layout = QVBoxLayout(self)
        layout.addWidget(self.waveform, 1)
        layout.addLayout(grid)
        layout.addLayout(buttons)

        self._fields = (
            self.start,
            self.end,
            self.fade_in,
            self.end_fade,
            self.repeats,
            self.loop_forever,
            self.stop_fade,
            self.loop_section,
            *self._section_fields,
            *self._loop_fade_fields,
            self.then,
            self.then_delay,
        )
        self.set_cue(None)

    # ----------------------------------------------------------------- state in
    @property
    def cue(self) -> AudioCue | None:
        return self._cue

    @property
    def duration(self) -> float | None:
        if self._audio_duration is not None:
            return self._audio_duration
        return None if self._cue is None else self._cue.file_duration

    def set_audio(self, audio: LoadedAudio | None, message: str = "") -> None:
        if audio is None:
            self._audio_duration = None
            self.waveform.clear(message)
        else:
            self._audio_duration = audio.duration
            self.waveform.set_audio(
                audio.peaks, PEAK_BUCKET_FRAMES / audio.samplerate, audio.duration
            )
        self.preview.setEnabled(audio is not None)
        self.set_cue(self._cue)

    def set_cue(self, cue: AudioCue | None) -> None:
        if cue is not self._cue:
            self.waveform.set_preview_from(cue.start_time if cue else 0.0)
            self._audio_duration = None  # new file: the main window calls set_audio next
        self._cue = cue
        for w in self._fields:
            w.blockSignals(True)
        try:
            if cue is None:
                self.length.clear()
                self.preview_from.clear()
                return
            dur = self.duration
            top = dur if dur is not None else 24 * 3600
            end_eff = cue.effective_end if cue.effective_end is not None else top
            self.start.setMaximum(top)
            self.end.setMaximum(top)
            self.start.setValue(cue.start_time)
            self.end.setValue(end_eff)
            region = max(0.0, end_eff - cue.start_time)
            self.length.setText(f"Length {format_time(region)}" if dur is not None else "")
            self.fade_in.setValue(cue.fade_in)
            self.end_fade.setValue(cue.end_fade)
            self.loop_forever.setChecked(cue.is_infinite)
            self.repeats.setEnabled(not cue.is_infinite)
            if not cue.is_infinite:
                self.repeats.setValue(cue.repeats)
            self.stop_fade.setValue(cue.stop_fade)
            self._show_section(cue, top)
            fades_on = self._show_loop_fades(cue)
            self.then.setCurrentIndex(max(0, self.then.findData(cue.continue_mode)))
            self.then_delay.setValue(cue.continue_delay)
            self.then_delay.setEnabled(cue.continue_mode != NO_CONTINUE)
            self.waveform.set_markers(cue.start_time, cue.end_time, cue.fade_in, cue.end_fade)
            self.waveform.set_section(
                cue.loop_section,
                cue.loop_start,
                cue.loop_end,
                cue.loop_after == AFTER_OUTRO,
                cue.loop_first != FIRST_INTRO,
            )
            self.waveform.set_loop_fades(
                fades_on, cue.loop_fade_in, cue.loop_fade_out, cue.loop_fade_mode
            )
            self.preview_from.setText(f"from {format_time(self.waveform.preview_from)}")
        finally:
            for w in self._fields:
                w.blockSignals(False)

    def set_preview_playing(self, playing: bool) -> None:
        self.preview.blockSignals(True)
        self.preview.setChecked(playing)
        self.preview.setText("■ Stop preview" if playing else "▶ Preview")
        self.preview.blockSignals(False)

    # ----------------------------------------------------------------- edits out
    def _emit(self, attr: str, value: Any, label: str, merge_key: object = None) -> None:
        if self._cue is not None and getattr(self._cue, attr) != value:
            self.edit.emit(attr, value, label, merge_key)

    def _end_eff(self) -> float | None:
        if self._cue is None:
            return None
        return self._cue.effective_end if self._cue.effective_end is not None else self.duration

    def _start_typed(self, v: float) -> None:
        end = self._end_eff()
        if end is not None:
            v = min(v, end - MIN_REGION_S)
        self._emit("start_time", round(max(0.0, v), 3), "start")
        self.set_cue(self._cue)  # show the clamped value

    def _end_typed(self, v: float) -> None:
        if self._cue is None:
            return
        v = max(v, self._cue.start_time + MIN_REGION_S)
        dur = self.duration
        value: float | None = round(v, 3)
        if dur is not None and v >= dur - END_SNAP_S:
            value = None
        self._emit("end_time", value, "end")
        self.set_cue(self._cue)

    def _fade_typed(self, attr: str, v: float) -> None:
        end = self._end_eff()
        if self._cue is not None and end is not None:
            v = min(v, end - self._cue.start_time)
        self._emit(attr, round(max(0.0, v), 3), WAVE_LABELS[attr])
        self.set_cue(self._cue)

    def _show_section(self, cue: AudioCue, top: float) -> None:
        """Repeat-section fields: the effective (clamped) loop points, enabled when on."""
        on = cue.loop_section
        self.loop_section.setChecked(on)
        end_eff = cue.effective_end if cue.effective_end is not None else top
        lo = min(max(cue.loop_start, cue.start_time), end_eff)
        hi = end_eff if cue.loop_end is None else min(max(cue.loop_end, lo), end_eff)
        for box in (self.loop_start, self.loop_end):
            box.setMaximum(top)
        self.loop_start.setValue(lo)
        self.loop_end.setValue(hi)
        self.loop_first.setCurrentIndex(max(0, self.loop_first.findData(cue.loop_first)))
        self.loop_after.setCurrentIndex(max(0, self.loop_after.findData(cue.loop_after)))
        for w in self._section_fields:
            w.setEnabled(on)

    def loop_fades_available(self, cue: AudioCue) -> bool:
        """Loop fades can be edited on a repeating cue with a section, or without one
        when the preference allows it (or the cue already has some, so they show)."""
        if cue.repeats == 0:
            return False
        return cue.loop_section or self.loop_fades_whole or cue.has_loop_fades

    def _show_loop_fades(self, cue: AudioCue) -> bool:
        on = self.loop_fades_available(cue)
        xf = cue.loop_fade_mode == LOOP_FADE_CROSSFADE
        self.loop_fade_mode.setCurrentIndex(
            max(0, self.loop_fade_mode.findData(cue.loop_fade_mode))
        )
        self.loop_fade_mode.setToolTip(LOOP_FADE_TIPS.get(cue.loop_fade_mode, ""))
        self.loop_fade_in.setValue(cue.loop_fade_in)
        self.loop_fade_out.setValue(cue.loop_fade_out)
        self.loop_fade_out_label.setText("Length" if xf else "Fade out")
        self.loop_fade_out.setToolTip(
            "Crossfade length at each jump back"
            if xf
            else "Fade down before each jump back to Loop start"
        )
        self.loop_fade_mode.setEnabled(on)
        self.loop_fade_out.setEnabled(on)
        self.loop_fade_in.setEnabled(on and not xf)
        self.loop_fade_in_label.setEnabled(on and not xf)
        self.loop_fade_out_label.setEnabled(on)
        return on

    def _loop_region(self) -> tuple[float, float] | None:
        cue, end = self._cue, self._end_eff()
        if cue is None or end is None:
            return None
        if not cue.loop_section:
            return cue.start_time, end
        lo = min(max(cue.loop_start, cue.start_time), end)
        hi = end if cue.loop_end is None else min(max(cue.loop_end, lo), end)
        return lo, hi

    def _loop_fade_typed(self, attr: str, v: float) -> None:
        region = self._loop_region()
        if self._cue is not None and region is not None:
            lo, hi = region
            top = hi - lo
            if attr == "loop_fade_out" and self._cue.loop_fade_mode == LOOP_FADE_CROSSFADE:
                top = min(top, lo)  # a crossfade needs that much audio before Loop start
            v = min(v, max(0.0, top))
        self._emit(attr, round(max(0.0, v), 3), WAVE_LABELS[attr])
        self.set_cue(self._cue)

    def _section_toggled(self, on: bool) -> None:
        cue = self._cue
        end = self._end_eff()
        if cue is None:
            return
        untouched = cue.loop_start <= cue.start_time and cue.loop_end is None
        if on and untouched and end is not None and end > cue.start_time:
            # First time on: put the loop lines inside the track (a third and two thirds
            # in), so they don't sit on top of Start and End.
            third = (end - cue.start_time) / 3
            self.edit_group.emit(
                [
                    ("loop_section", True),
                    ("loop_start", round(cue.start_time + third, 3)),
                    ("loop_end", round(cue.start_time + 2 * third, 3)),
                ],
                "repeat section",
            )
        else:
            self._emit("loop_section", on, "repeat section")

    def _loop_start_typed(self, v: float) -> None:
        if self._cue is None:
            return
        end = self._end_eff()
        lo = max(self._cue.start_time, v)
        if end is not None:
            loop_end = end if self._cue.loop_end is None else min(self._cue.loop_end, end)
            lo = min(lo, loop_end - MIN_REGION_S)
        self._emit("loop_start", round(max(0.0, lo), 3), "loop start")
        self.set_cue(self._cue)

    def _loop_end_typed(self, v: float) -> None:
        if self._cue is None:
            return
        end = self._end_eff()
        hi: float | None = round(max(v, self._cue.loop_start + MIN_REGION_S), 3)
        if end is not None and v >= end - END_SNAP_S:
            hi = None  # runs to the cue's End, and follows it if End moves
        self._emit("loop_end", hi, "loop end")
        self.set_cue(self._cue)

    def _then_changed(self, _index: int) -> None:
        mode = self.then.currentData()
        self.then_delay.setEnabled(mode != NO_CONTINUE)
        self._emit("continue_mode", mode, "then")

    def _repeats_typed(self, n: int) -> None:
        self._emit("repeats", n, "repeats")

    def _loop_toggled(self, on: bool) -> None:
        self.repeats.setEnabled(not on)
        self._emit("repeats", INFINITE_REPEATS if on else self.repeats.value(), "repeats")

    def _wave_edit(self, attr: str, value: object, gesture: int) -> None:
        self._emit(attr, value, WAVE_LABELS[attr], ("wave", attr, gesture))

    def _preview_from_changed(self, t: float) -> None:
        self.preview_from.setText(f"from {format_time(t)}")
        if self.preview.isChecked():  # re-cue a running preview to the new point
            self.preview_requested.emit(t)

    def _preview_clicked(self, checked: bool) -> None:
        if checked:
            self.preview_requested.emit(self.waveform.preview_from)
        else:
            self.preview_stop_requested.emit()
