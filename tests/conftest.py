"""Shared fixtures."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from winq.ui.main_window import MainWindow


@pytest.fixture
def make_window(qtbot, tmp_path_factory):
    """Factory for MainWindows that never open the real audio device, never touch the
    real WinQ settings/autosave folder, and never block on the "save changes?" prompt
    at teardown. Drive audio with `w.engine.mixer.render(n)`.

    The windows are deliberately NOT registered with qtbot.addWidget: pytest-qt closes
    those before fixture teardown, which would pop the unsaved-changes dialog and hang.
    Pass `settings_dir` to share settings/autosaves between windows (e.g. to simulate
    a restart after a crash).
    """
    windows: list[MainWindow] = []

    def make(settings_dir=None) -> MainWindow:
        d = settings_dir or tmp_path_factory.mktemp("winq-settings")
        settings = QSettings(str(d / "winq.ini"), QSettings.Format.IniFormat)
        w = MainWindow(start_audio=False, settings=settings, autosave_dir=d / "autosave")
        # Tests press GO in quick succession and close windows mid-show; the double-GO
        # guard and the quit confirmation are tested explicitly in test_prefs.py.
        w.prefs.go_guard = 0.0
        w.confirm_quit = False
        windows.append(w)
        return w

    yield make
    for w in windows:
        w.undo_stack.setClean()
        w.confirm_quit = False
        w.close()  # closeEvent → shutdown()
        w.deleteLater()
