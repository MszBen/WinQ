"""End-to-end through the real MainWindow, CuePlayer and Mixer (no audio device):
GO → voice in the mixer → rendered audio → VoiceEnded → transport and UI updated."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import Qt

from winq.audio.cue_player import AudioCache, CuePlayer
from winq.model.cue import INFINITE_REPEATS
from winq.ui.cue_table import Col


def write_tone(path: Path, seconds: float, sr: int = 48000, value: float = 0.5) -> Path:
    sf.write(path, np.full((int(seconds * sr), 1), value, dtype=np.float32), sr)
    return path


def render_seconds(w, seconds: float) -> np.ndarray:
    sr = w.engine.samplerate
    blocks = [w.engine.mixer.render(512) for _ in range(int(seconds * sr / 512) + 1)]
    return np.concatenate(blocks)


@pytest.fixture
def window(make_window, tmp_path):
    w = make_window()
    w.add_files(
        [
            write_tone(tmp_path / "a.wav", 0.2),
            write_tone(tmp_path / "b.wav", 0.2),
            write_tone(tmp_path / "loop.wav", 0.1),
        ]
    )
    w.cue_model.cue_list[2].repeats = INFINITE_REPEATS
    w.cue_view.select_row(0)
    return w


def test_go_plays_and_cue_ends(window):
    w = window
    w.go()
    assert w.cue_view.selected_row() == 1
    assert w.cue_model.data(w.cue_model.index(0, Col.STATUS)) == "▶"
    out = render_seconds(w, 0.1)
    assert np.abs(out).max() > 0.4  # sound is coming out
    render_seconds(w, 0.2)
    w._tick()
    assert w.transport.running == ()
    assert w.cue_model.data(w.cue_model.index(0, Col.STATUS)) is None


def test_two_cues_overlap_and_escape_stops_oldest_first(window):
    w = window
    w.go()
    w.go()
    render_seconds(w, 0.02)
    w._tick()
    assert [r.cue.name for r in w.transport.running] == ["a", "b"]
    w.cue_model.cue_list[0].stop_fade = 0.0
    w.stop_oldest()
    render_seconds(w, 0.02)
    w._tick()
    assert [r.cue.name for r in w.transport.running] == ["b"]


def test_infinite_cue_loop_end_flow(window):
    w = window
    w.cue_view.select_row(2)
    w.go()
    assert w.cue_view.selected_row() == 2  # stays on the looping cue
    assert "Looping" in w.transport_bar.hint.text()
    render_seconds(w, 0.5)  # many passes
    w._tick()
    assert len(w.transport.running) == 1
    w.go()  # end the loop
    assert w.transport_bar.hint.text() == ""
    render_seconds(w, 0.15)  # at most one more pass
    w._tick()
    assert w.transport.running == ()


def test_missing_file_is_skipped_with_warning(window):
    w = window
    w.cue_model.cue_list[0].file_duration = None
    w.go()
    assert w.transport.running == ()
    assert w.cue_view.selected_row() == 1
    assert "Skipped" in w.transport_bar.warning.text()


def test_panic(window):
    w = window
    w.go()
    w.go()
    w.panic()
    render_seconds(w, w.panic_fade + 0.05)
    w._tick()
    assert w.transport.running == ()


def test_active_panel_lists_in_escape_order(window):
    w = window
    w.go()
    w.go()
    render_seconds(w, 0.01)
    w._tick()
    rows = w.active_panel._row_widgets
    assert [r.name.text() for r in rows] == ["1 a", "2 b"]
    assert rows[0].time.text().startswith("-0:00.")


def test_space_and_escape_keys(window, qtbot):
    w = window
    w.show()
    w.cue_view.setFocus()
    qtbot.keyClick(w.cue_view, Qt.Key.Key_Space)
    assert len(w.transport.running) == 1
    qtbot.keyClick(w.cue_view, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
    assert len(w.transport.running) == 1  # Ctrl+Space is not GO
    qtbot.keyClick(w.cue_view, Qt.Key.Key_Escape)
    assert w.transport.running[0].stopping

    # While typing in a table editor, Space types instead of firing GO.
    w.cue_view.edit(w.cue_model.index(1, Col.NAME))
    editor = w.focusWidget()
    qtbot.keyClick(editor, Qt.Key.Key_Space)
    assert len(w.transport.running) == 1


def test_cue_player_maps_trim_and_level(tmp_path):
    from winq.audio.engine import Mixer
    from winq.model.cue import AudioCue

    class _Engine:
        samplerate = 1000
        mixer = Mixer(1000)
        _id = 0

        def next_voice_id(self):
            self._id += 1
            return self._id

        def play(self, voice, **_kw):
            self.voice = voice

    path = write_tone(tmp_path / "t.wav", 2.0, sr=1000)
    engine = _Engine()
    player = CuePlayer(engine, AudioCache(1000))
    cue = AudioCue(file_path=path, start_time=0.5, end_time=1.25, level_db=-6, repeats=1)
    cue.file_duration = 2.0
    vid = player.start(cue)
    v = engine.voice
    assert (v.start_frame, v.end_frame, v.repeats) == (500, 1250, 1)
    assert v.gain == pytest.approx(0.501, abs=1e-3)
    p = player.progress(vid)
    assert p.remaining == pytest.approx(1.5) and p.pass_fraction == 0.0
    player.cache.shutdown()
