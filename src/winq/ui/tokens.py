"""Design tokens for painted widgets: the "blue-lit booth" palette and the numeric face.

theme.qss uses the same hex values (QSS has no variables); keep the two in step.
Colour always means state, never decoration:
    lamp green = playing / GO, sodium amber = looping, fades, Show mode,
    stop red = stop / end / missing, cue cyan = waiting / preview.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont

# Booth surfaces, darkest first.
BOOTH_0 = "#0d1119"  # fields, waveform, meters, status bar
BOOTH_1 = "#131824"  # window
BOOTH_2 = "#181e2c"  # panels
BOOTH_3 = "#212839"  # raised: rows, headers, tabs
LINE = "#2c3449"

TEXT = "#dde2ee"
TEXT_MUTED = "#8a93ab"
TEXT_DIM = "#586079"

LAMP = "#3cc46a"
AMBER = "#f0a830"
STOP = "#e5484d"
CYAN = "#3fc4d4"
LOOP = "#5aa9ff"  # repeat-section handles and band
STANDBY = "#34418f"

WAVE = "#6f7bbd"
WAVE_OUT = "#2a3150"

NUMERIC_FAMILY = "Bahnschrift"


def color(hex_value: str, alpha: int = 255) -> QColor:
    c = QColor(hex_value)
    c.setAlpha(alpha)
    return c


def numeric_font(base: QFont | None = None, *, point_size: float | None = None) -> QFont:
    """Bahnschrift with tabular figures, so running times don't jitter as digits change."""
    font = QFont(base) if base is not None else QFont()
    font.setFamilies([NUMERIC_FAMILY, "Segoe UI"])
    font.setFeature(QFont.Tag("tnum"), 1)
    if point_size is not None:
        font.setPointSizeF(point_size)
    return font
