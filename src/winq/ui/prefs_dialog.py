"""Preferences dialog: Audio, Show, New cues, Display and Files tabs."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from winq.audio.devices import OutputDevice
from winq.model.cue import MAX_LEVEL_DB
from winq.ui.app_settings import (
    ACTIVE_TIME_CHOICES,
    AUTOSAVE_CHOICES,
    BUFFER_LABELS,
    GO_GUARD_CHOICES,
    UI_SCALES,
    AppPrefs,
)
from winq.ui.time_loops import THEN_CHOICES

MIN_LEVEL_DB = -60.0  # shown as -inf, like the cue level control
FIELD_WIDTH = 110  # number fields


def device_key(d: OutputDevice) -> str:
    """Stable id for a device across launches (PortAudio indices can change)."""
    return f"{d.host_api}|{d.name}"


def _seconds(value: float, maximum: float) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0.0, maximum)
    box.setDecimals(2)
    box.setSingleStep(0.1)
    box.setSuffix(" s")
    box.setAlignment(Qt.AlignmentFlag.AlignRight)
    box.setFixedWidth(FIELD_WIDTH)
    box.setValue(value)
    return box


def _combo(items: list[tuple[object, str]], current: object) -> QComboBox:
    box = QComboBox()
    for data, text in items:
        box.addItem(text, data)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("panelEmpty")
    return label


def _page(form: QFormLayout, *notes: str) -> QWidget:
    # Fields keep their natural width instead of stretching across the dialog.
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.addLayout(form)
    for text in notes:
        layout.addWidget(_note(text))
    layout.addStretch(1)
    return page


class PreferencesDialog(QDialog):
    test_tone_requested = Signal()

    def __init__(
        self,
        devices: list[OutputDevice],
        current_device: int | None,
        prefs: AppPrefs | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(520)
        self._prefs = prefs if prefs is not None else AppPrefs()
        p = self._prefs

        # Audio ----------------------------------------------------------
        self.device = QComboBox()
        # WASAPI first (lowest latency in shared mode), then everything else.
        ordered = sorted(
            devices, key=lambda d: (d.host_api != "Windows WASAPI", d.host_api, d.name)
        )
        for d in ordered:
            self.device.addItem(f"{d.name}  ({d.host_api})", d.index)
        if current_device is not None and (i := self.device.findData(current_device)) >= 0:
            self.device.setCurrentIndex(i)
        self.buffer = _combo(list(BUFFER_LABELS.items()), p.buffer)
        self.test_tone = QPushButton("Play test tone")
        self.test_tone.setToolTip("A short tone on the left speaker, then on the right")
        self.test_tone.clicked.connect(self.test_tone_requested)
        audio = QFormLayout()
        audio.addRow("Output device", self.device)
        audio.addRow("Buffer", self.buffer)
        audio.addRow("", self.test_tone)
        audio_page = _page(
            audio,
            "Changing the device or buffer stops everything that is playing. Windows "
            "WASAPI devices have the lowest latency.",
            "The test tone plays on the output in use now. To test another device, "
            "choose it and press OK first.",
        )

        # Show -----------------------------------------------------------
        self.panic_fade = _seconds(p.panic_fade, 10.0)
        self.go_guard = _combo(
            [(g, "Off" if g == 0 else f"{g:g} s") for g in GO_GUARD_CHOICES], p.go_guard
        )
        self.open_in_show_mode = QCheckBox("Open shows in Show mode")
        self.open_in_show_mode.setChecked(p.open_in_show_mode)
        show = QFormLayout()
        show.addRow("Stop All (Shift+Esc) fade", self.panic_fade)
        show.addRow("Double-GO guard", self.go_guard)
        show.addRow("", self.open_in_show_mode)
        show_page = _page(
            show,
            "Double-GO guard: a second GO within this time of the last one is ignored, so "
            "a nervous double tap doesn't fire two cues.",
        )

        # New cues -------------------------------------------------------
        self.cue_level = QDoubleSpinBox()
        self.cue_level.setRange(MIN_LEVEL_DB, MAX_LEVEL_DB)
        self.cue_level.setDecimals(1)
        self.cue_level.setSingleStep(0.5)
        self.cue_level.setSuffix(" dB")
        self.cue_level.setSpecialValueText("-inf dB")
        self.cue_level.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.cue_level.setFixedWidth(FIELD_WIDTH)
        self.cue_level.setValue(p.cue_level_db)
        self.cue_fade_in = _seconds(p.cue_fade_in, 600.0)
        self.cue_end_fade = _seconds(p.cue_end_fade, 600.0)
        self.cue_stop_fade = _seconds(p.cue_stop_fade, 60.0)
        self.cue_then = _combo(list(THEN_CHOICES), p.cue_then)
        cues = QFormLayout()
        cues.addRow("Level", self.cue_level)
        cues.addRow("Fade in", self.cue_fade_in)
        cues.addRow("End fade", self.cue_end_fade)
        cues.addRow("Stop fade (Esc)", self.cue_stop_fade)
        cues.addRow("Then", self.cue_then)
        cues_page = _page(
            cues,
            "Used for cues added from now on. Existing cues keep their settings. Fades "
            "longer than a file are shortened to fit it.",
        )

        # Display --------------------------------------------------------
        self.ui_scale = _combo([(s, f"{s} %") for s in UI_SCALES], p.ui_scale)
        self.show_notes = QCheckBox("Show the standby cue's notes in the GO bar")
        self.show_notes.setChecked(p.show_notes)
        self.loop_fades_whole = QCheckBox("Loop fades also for whole-track repeats")
        self.loop_fades_whole.setToolTip(
            "Off: loop fades are set only on cues with 'Repeat only a section'. "
            "On: any repeating cue can fade (or crossfade) where it jumps back to Start."
        )
        self.loop_fades_whole.setChecked(p.loop_fades_whole)
        self.active_time = _combo(list(ACTIVE_TIME_CHOICES.items()), p.active_time)
        display = QFormLayout()
        display.addRow("UI size", self.ui_scale)
        display.addRow("Active Cues shows", self.active_time)
        display.addRow("", self.show_notes)
        display.addRow("", self.loop_fades_whole)
        display_page = _page(display, "UI size applies the next time WinQ starts.")

        # Files ----------------------------------------------------------
        self.reopen_last = QCheckBox("Reopen the last show when WinQ starts")
        self.reopen_last.setChecked(p.reopen_last)
        self.autosave = _combo(list(AUTOSAVE_CHOICES.items()), p.autosave_seconds)
        files = QFormLayout()
        files.addRow("", self.reopen_last)
        files.addRow("Autosave backup", self.autosave)
        files_page = _page(
            files,
            "Autosave writes a separate backup of unsaved changes (your .winq file is "
            "never overwritten). It is what lets WinQ recover after a crash.",
        )

        self.tabs = QTabWidget()
        self.tabs.setObjectName("prefsTabs")
        for page, title in (
            (audio_page, "Audio"),
            (show_page, "Show"),
            (cues_page, "New cues"),
            (display_page, "Display"),
            (files_page, "Files"),
        ):
            self.tabs.addTab(page, title)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    def selected_device(self) -> int | None:
        return self.device.currentData()

    def selected_panic_fade(self) -> float:
        return round(self.panic_fade.value(), 3)

    def selected_prefs(self) -> AppPrefs:
        return replace(
            self._prefs,
            buffer=self.buffer.currentData(),
            panic_fade=self.selected_panic_fade(),
            go_guard=self.go_guard.currentData(),
            open_in_show_mode=self.open_in_show_mode.isChecked(),
            cue_level_db=round(self.cue_level.value(), 1),
            cue_fade_in=round(self.cue_fade_in.value(), 3),
            cue_end_fade=round(self.cue_end_fade.value(), 3),
            cue_stop_fade=round(self.cue_stop_fade.value(), 3),
            cue_then=self.cue_then.currentData(),
            ui_scale=self.ui_scale.currentData(),
            show_notes=self.show_notes.isChecked(),
            loop_fades_whole=self.loop_fades_whole.isChecked(),
            active_time=self.active_time.currentData(),
            reopen_last=self.reopen_last.isChecked(),
            autosave_seconds=self.autosave.currentData(),
        )
