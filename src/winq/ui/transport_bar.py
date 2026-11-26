"""Top bar: big GO button, standing-by readout, warning line, Stop All (panic)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from winq.model.cue import MAX_MASTER_DB, AudioCue
from winq.ui.elided_label import ElidedLabel
from winq.ui.level_control import LevelControl, PeakMeter


class TransportBar(QFrame):
    go_clicked = Signal()
    panic_clicked = Signal()
    mode_toggled = Signal(bool)  # True = Show mode

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("transportBar")
        self.show_notes = True  # the standby cue's notes go in the hint line (a preference)

        self.go_button = QPushButton("GO")
        self.go_button.setObjectName("goButton")
        self.go_button.setToolTip("Space")
        self.go_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space must never "click" it
        self.go_button.clicked.connect(self.go_clicked)

        self.standby_caption = QLabel("Standing by")
        self.standby_caption.setObjectName("standbyCaption")
        self.standby = ElidedLabel()
        self.standby.setObjectName("standbyLabel")
        self.hint = ElidedLabel()
        self.hint.setObjectName("standbyHint")
        self.warning = ElidedLabel()
        self.warning.setObjectName("warningLabel")

        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(self.standby_caption)
        text.addWidget(self.standby)
        text.addWidget(self.hint)
        text.addWidget(self.warning)

        self.panic_button = QPushButton("Stop All")
        self.panic_button.setObjectName("panicButton")
        self.panic_button.setToolTip("Shift+Esc")
        self.panic_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.panic_button.clicked.connect(self.panic_clicked)

        self.mode_button = QPushButton("EDIT")
        self.mode_button.setObjectName("modeButton")
        self.mode_button.setCheckable(True)
        self.mode_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode_button.setToolTip("Edit / Show mode (Ctrl+M). Show mode locks all editing.")
        self.mode_button.toggled.connect(self.mode_toggled)

        self.master = LevelControl(max_db=MAX_MASTER_DB)
        self.master.setFixedWidth(260)
        self.master.setToolTip("Master level (saved with the show)")
        self.meter = PeakMeter()
        master_title = QLabel("Master")
        master_title.setObjectName("panelTitle")
        master_box = QVBoxLayout()
        master_box.setSpacing(2)
        master_box.addWidget(master_title)
        master_box.addWidget(self.master)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 14, 10)
        layout.setSpacing(16)
        layout.addWidget(self.go_button)
        layout.addLayout(text, 1)
        layout.addLayout(master_box)
        layout.addWidget(self.meter)
        layout.addWidget(self.panic_button)
        layout.addWidget(self.mode_button)

        self._warning_timer = QTimer(self)
        self._warning_timer.setSingleShot(True)
        self._warning_timer.timeout.connect(self._warning_expired)
        self._alert = ""  # a lasting problem (e.g. audio output lost), shown under warnings

        self.set_standby(None, looping=False)

    def set_show_mode(self, on: bool) -> None:
        self.mode_button.blockSignals(True)
        self.mode_button.setChecked(on)
        self.mode_button.setText("SHOW" if on else "EDIT")
        self.mode_button.blockSignals(False)

    def set_standby(self, cue: AudioCue | None, *, looping: bool) -> None:
        if cue is None:
            self.standby.setText("—")
            self.hint.setText("")
            self._restyle_hint(False, False)
            return
        notes = False
        self.standby.setText(f"{cue.number} {cue.name}" if cue.number else cue.name)
        if looping:
            self.hint.setText("∞ Looping: Space ends the loop (it finishes its current pass)")
        elif cue.is_missing:
            self.hint.setText("File missing: Space will skip this cue")
        elif self.show_notes and cue.notes.strip():
            # Every note line on one line; the label shortens it with "…" if needed.
            lines = [line.strip() for line in cue.notes.splitlines() if line.strip()]
            self.hint.setText("✎ " + " · ".join(lines))
            notes = True
        else:
            self.hint.setText("")
        self._restyle_hint(looping, notes)

    def _restyle_hint(self, looping: bool, notes: bool) -> None:
        self.hint.setProperty("looping", looping)
        self.hint.setProperty("notes", notes)
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)

    def show_warning(self, text: str, seconds: float = 5.0) -> None:
        self.warning.setText(text)
        self._warning_timer.start(int(seconds * 1000))

    def set_alert(self, text: str) -> None:
        """A lasting warning that stays until cleared with "" (brief warnings from
        show_warning take its place for a few seconds, then it comes back)."""
        self._alert = text
        if not self._warning_timer.isActive():
            self.warning.setText(text)

    @property
    def alert(self) -> str:
        return self._alert

    def _warning_expired(self) -> None:
        self.warning.setText(self._alert)
