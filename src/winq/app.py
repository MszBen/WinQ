"""Application bootstrap: creates the QApplication, applies the theme, shows the main window.

Command line:
    winq [SHOW.winq]       open that show (instead of reopening the last one)
    winq --smoke-test      start, open the audio device, exit 0 if all is well (used to
                           check a built WinQ.exe; no windows are left open)
"""

from __future__ import annotations

import contextlib
import sys
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from winq import __version__, crash_log
from winq.ui.app_settings import apply_ui_scale
from winq.ui.main_window import MainWindow
from winq.ui.single_instance import InstanceServer, send_to_running


def load_theme() -> str:
    """Return the dark theme stylesheet bundled with the package."""
    return resources.files("winq.ui").joinpath("theme.qss").read_text(encoding="utf-8")


def load_icon() -> QIcon:
    """The WinQ logo, rendered from the bundled SVG at the sizes Windows asks for."""
    svg = resources.files("winq.ui").joinpath("winq.svg").read_bytes()
    renderer = QSvgRenderer(QByteArray(svg))
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _set_windows_app_id() -> None:
    """Group WinQ under its own taskbar button and icon (not python.exe's) when run from source."""
    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WinQ.WinQ")


def create_app(argv: list[str] | None = None) -> QApplication:
    _set_windows_app_id()
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("WinQ")
    app.setOrganizationName("WinQ")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(load_icon())
    app.setStyle("Fusion")  # consistent base for the stylesheet across Windows versions
    app.setStyleSheet(load_theme())
    return app


def smoke_test(app: QApplication) -> int:
    """Open the real audio device for a moment; exit code 0 = engine ran, 1 = not."""
    window = MainWindow()
    window.show()
    result = {"code": 1}

    def check() -> None:
        engine = window.engine
        ok = engine is not None and engine.callbacks > 0
        result["code"] = 0 if ok else 1
        window.undo_stack.setClean()
        window.close()
        app.quit()

    QTimer.singleShot(1500, check)
    app.exec()
    return result["code"]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    crash_log.install(__version__)  # unexpected errors → log file; WinQ keeps running
    apply_ui_scale()  # Preferences → Display → UI size; must precede the QApplication
    app = create_app(argv)
    if "--smoke-test" in argv:
        return smoke_test(app)
    show_arg = next((Path(a) for a in argv[1:] if a.lower().endswith(".winq")), None)
    # One WinQ per user: hand the show to the running one (it comes to the front).
    if send_to_running(str(show_arg.resolve()) if show_arg else None):
        return 0
    instance = InstanceServer()
    window = MainWindow()
    instance.activate_requested.connect(window.activate_from_other_launch)
    crash_log.on_error(window.error_logged.emit)  # thread-safe: queued to the GUI
    window.show()
    QTimer.singleShot(0, lambda: window.startup(show_arg))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
