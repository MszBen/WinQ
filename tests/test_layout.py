"""The window must fit a laptop screen sideways: no horizontal scrolling anywhere, and
long names or paths shorten with "…" instead of widening the window."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtWidgets import QAbstractScrollArea

from winq.ui.cue_table import Col
from winq.ui.elided_label import ElidedLabel

LONG_NAME = "A rather long cue name for the second act storm ambience loop take 3 " * 2
LAPTOP_MIN_WIDTH = 1024  # logical px: 1280x720 at 150 %, 1280x800 at 200 % all exceed this


@pytest.fixture
def long_window(make_window, tmp_path):
    folder = tmp_path / ("very long folder name " * 4).strip()
    folder.mkdir()
    path = folder / f"{LONG_NAME.strip()}.wav"
    sf.write(path, np.zeros((4800, 2), np.float32), 48000)
    w = make_window()
    w.add_files([path])
    w.cue_view.select_row(0)
    w.show()
    return w


def test_window_minimum_width_fits_a_laptop(long_window):
    w = long_window
    assert w.minimumSizeHint().width() <= LAPTOP_MIN_WIDTH
    # The long name is shown shortened, but the full text is kept (and in the tooltip).
    assert w.transport_bar.standby.text().endswith(LONG_NAME.strip())


@pytest.mark.parametrize("size", [(1024, 640), (1280, 720), (1366, 768)])
def test_no_horizontal_scrollbars_at_laptop_sizes(long_window, qapp, size):
    w = long_window
    free = w.screen().availableGeometry()
    if size[0] > free.width() or size[1] > free.height():
        pytest.skip("Windows won't size a window beyond this machine's screen")
    w.resize(*size)
    for tab in range(w.inspector.count()):
        w.inspector.setCurrentIndex(tab)
        qapp.processEvents()
        for area in w.findChildren(QAbstractScrollArea):
            assert not area.horizontalScrollBar().isVisible(), type(area).__name__


def test_cue_table_columns_fill_the_view_exactly(long_window, qapp):
    w = long_window
    for width in (1024, 1600):
        w.resize(width, 720)
        qapp.processEvents()
        header = w.cue_view.horizontalHeader()
        total = sum(header.sectionSize(c) for c in Col)
        assert total <= w.cue_view.viewport().width()
        assert header.sectionSize(Col.NAME) > header.sectionSize(Col.FILE)


def test_elided_label_shortens_but_keeps_full_text(qapp):
    label = ElidedLabel("x" * 300)
    label.resize(80, 20)
    assert label.text() == "x" * 300
    assert label.toolTip() == "x" * 300
    assert label.minimumSizeHint().width() < 80


def test_active_cues_empty_text_is_centred_in_the_panel(make_window, qapp):
    w = make_window()
    w.resize(1280, 720)
    w.show()
    qapp.processEvents()
    panel, empty = w.active_panel, w.active_panel._empty
    centre = empty.mapTo(panel, empty.rect().center())
    title_bottom = panel.findChild(type(empty), None).geometry().bottom()
    assert abs(centre.x() - panel.width() / 2) <= 2
    # Centred in the space under the title, not stuck at the top.
    assert abs(centre.y() - (title_bottom + panel.height()) / 2) <= 12


@pytest.fixture
def themed(qapp):
    """The real dark theme (the lines and frames being compared come from it)."""
    from winq.app import load_theme

    old = qapp.styleSheet()
    qapp.setStyleSheet(load_theme())
    yield
    qapp.setStyleSheet(old)


def test_active_cues_title_line_lines_up_with_cue_list_header(make_window, qapp, themed):
    w = make_window()
    w.resize(1280, 720)
    w.show()
    qapp.processEvents()
    header, title = w.cue_view.horizontalHeader(), w.active_panel.title
    assert (
        header.mapTo(w, header.rect().bottomLeft()).y()
        == title.mapTo(w, title.rect().bottomLeft()).y()
    )
