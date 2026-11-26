"""User preferences (QSettings), as one typed object.

The output device is stored separately (`audio/device`, see prefs_dialog.device_key).
Keys are read defensively: an .ini file returns strings, and old or hand-edited values
fall back to the default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

from PySide6.QtCore import QSettings

from winq.model.cue import CONTINUE, DEFAULT_PANIC_FADE, DEFAULT_STOP_FADE, FOLLOW, NO_CONTINUE

# Audio buffer: PortAudio latency per choice. "low" is the old fixed setting.
BUFFER_CHOICES = {"low": "low", "normal": 0.05, "safe": 0.15}
BUFFER_LABELS = {
    "low": "Low (fastest response)",
    "normal": "Normal",
    "safe": "Safe (for Bluetooth or crackling)",
}
GO_GUARD_CHOICES = (0.0, 0.3, 0.5, 1.0)
UI_SCALES = (100, 115, 130)
ACTIVE_TIME_CHOICES = {"remaining": "Time remaining", "elapsed": "Time elapsed"}
AUTOSAVE_CHOICES = {0: "Off (no crash recovery)", 30: "Every 30 s", 60: "Every minute",
                    300: "Every 5 minutes"}  # fmt: skip
THEN_MODES = (NO_CONTINUE, FOLLOW, CONTINUE)


@dataclass
class AppPrefs:
    # Audio
    buffer: str = "low"
    # Show control
    panic_fade: float = DEFAULT_PANIC_FADE
    go_guard: float = 0.3  # seconds; a second GO this soon after the last is ignored
    open_in_show_mode: bool = False
    # New-cue defaults
    cue_level_db: float = 0.0
    cue_fade_in: float = 0.0
    cue_end_fade: float = 0.0
    cue_stop_fade: float = DEFAULT_STOP_FADE
    cue_then: str = NO_CONTINUE
    # Display
    ui_scale: int = 100  # percent; applied at the next launch
    show_notes: bool = True  # the standby cue's notes under its name in the GO bar
    active_time: str = "remaining"
    loop_fades_whole: bool = False  # loop-fade fields also for cues without a section
    # Files
    reopen_last: bool = True
    autosave_seconds: int = 30

    @property
    def latency(self) -> str | float:
        return BUFFER_CHOICES[self.buffer]


# Where each field lives. panic_fade keeps its original key so old settings still apply.
_KEYS = {f.name: f"prefs/{f.name}" for f in fields(AppPrefs)} | {"panic_fade": "panic_fade"}
_VALID = {
    "buffer": set(BUFFER_CHOICES),
    "go_guard": set(GO_GUARD_CHOICES),
    "ui_scale": set(UI_SCALES),
    "active_time": set(ACTIVE_TIME_CHOICES),
    "autosave_seconds": set(AUTOSAVE_CHOICES),
    "cue_then": set(THEN_MODES),
}


def _convert(raw: object, default: object) -> object:
    if isinstance(default, bool):
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if isinstance(default, int):
        return int(float(raw))  # type: ignore[arg-type]
    if isinstance(default, float):
        return float(raw)  # type: ignore[arg-type]
    return str(raw)


def load_prefs(settings: QSettings) -> AppPrefs:
    prefs = AppPrefs()
    for f in fields(AppPrefs):
        raw = settings.value(_KEYS[f.name])
        if raw is None or raw == "":
            continue
        default = getattr(prefs, f.name)
        try:
            value = _convert(raw, default)
        except (TypeError, ValueError):
            continue
        if f.name in _VALID and value not in _VALID[f.name]:
            continue
        setattr(prefs, f.name, value)
    return prefs


def save_prefs(settings: QSettings, prefs: AppPrefs) -> None:
    for f in fields(AppPrefs):
        settings.setValue(_KEYS[f.name], getattr(prefs, f.name))


def apply_ui_scale(settings: QSettings | None = None) -> int:
    """Set Qt's scale factor from the saved UI size. Must run before the QApplication
    is created. Returns the percentage applied."""
    settings = settings if settings is not None else QSettings("WinQ", "WinQ")
    scale = load_prefs(settings).ui_scale
    if scale != 100 and "QT_SCALE_FACTOR" not in os.environ:
        os.environ["QT_SCALE_FACTOR"] = f"{scale / 100:g}"
    return scale
