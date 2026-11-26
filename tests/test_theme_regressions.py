"""Visual regressions that tests can catch without screenshots."""

from __future__ import annotations

from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit

from winq.app import load_theme


def test_every_spin_box_shows_its_value(make_window, qtbot):
    """setButtonSymbols(NoButtons) plus our stylesheet once collapsed the text field of
    every spin box to 1 px, so values were invisible. The fields must stay readable."""
    app = QApplication.instance()
    app.setStyleSheet(load_theme())
    try:
        w = make_window()
        w.show()
        qtbot.waitExposed(w)
        spins = w.findChildren(QAbstractSpinBox)
        assert len(spins) >= 7  # master, level, start, end, fades, repeats, stop fade
        for spin in spins:
            spin.show()  # widgets on hidden tabs still need a sane layout
            spin.resize(spin.sizeHint().expandedTo(spin.size()))
            QApplication.processEvents()
            line_edit = spin.findChild(QLineEdit)
            assert line_edit.width() > 0.6 * spin.width(), type(spin).__name__
            assert spin.buttonSymbols() != QAbstractSpinBox.ButtonSymbols.NoButtons, (
                "hide buttons in theme.qss instead"
            )
    finally:
        app.setStyleSheet("")
