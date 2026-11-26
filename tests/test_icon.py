from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize

from winq.app import load_icon

ROOT = Path(__file__).resolve().parent.parent


def test_app_icon_renders_at_small_and_large_sizes(qapp) -> None:
    icon = load_icon()
    assert not icon.isNull()
    for size in (16, 32, 256):
        image = icon.pixmap(QSize(size, size), 1.0).toImage()  # 1.0: ignore HiDPI scaling
        assert image.width() == size
        # The centre of the logo is drawn (not transparent).
        assert image.pixelColor(size // 2, size // 4).alpha() > 0


def test_exe_icon_file_exists() -> None:
    ico = ROOT / "assets" / "winq.ico"
    assert ico.read_bytes()[:4] == b"\x00\x00\x01\x00"
