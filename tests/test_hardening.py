"""Launch hardening: a bad voice never silences the show, a lost output is reported,
bad files can't crash loading, autosave recovery leaves other open windows alone."""

from __future__ import annotations

import subprocess
import sys
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt

from winq.audio.engine import Mixer, Play, VoiceEnded
from winq.audio.loader import AudioLoadError, load_audio
from winq.audio.voice import Voice
from winq.model.show_file import ShowFileError, load_show
from winq.ui.cue_table import Col
from winq.ui.main_window import OUTPUT_LOST
from winq.win_process import pid_from_autosave_name, winq_process_alive

SR = 1000


def voice(vid: int, value: float = 0.25) -> Voice:
    return Voice(vid, np.full((10 * SR, 2), value, dtype=np.float32), SR)


def test_a_failing_voice_is_dropped_and_the_others_keep_playing():
    mixer = Mixer(SR)
    good, bad = voice(1), voice(2)

    def boom(frames: int) -> np.ndarray:
        raise RuntimeError("bug")

    bad.render = boom  # type: ignore[method-assign]
    mixer.send(Play(good))
    mixer.send(Play(bad))
    out = mixer.render(100)
    assert np.allclose(out, 0.25)  # the good voice is still heard
    assert mixer.voice_errors == 1
    assert mixer.active_voice_count == 1
    ended = [e for e in mixer.poll_events() if isinstance(e, VoiceEnded)]
    assert ended == [VoiceEnded(2, natural=False)]  # stopped, so nothing follows it


def test_unreadable_files_raise_audio_load_error(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"RIFF\x00\x00not really audio" * 10)
    with pytest.raises(AudioLoadError):
        load_audio(junk, 48000)


def test_binary_show_file_is_a_show_file_error(tmp_path):
    bad = tmp_path / "bad.winq"
    bad.write_bytes(bytes(range(256)) * 4)
    with pytest.raises(ShowFileError):
        load_show(bad)


def test_pid_from_autosave_name():
    assert pid_from_autosave_name("session-1234", "session-") == 1234
    assert pid_from_autosave_name("session-abc", "session-") is None
    assert pid_from_autosave_name("other-1", "session-") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process check")
def test_autosave_of_a_running_winq_is_not_offered_for_recovery(make_window):
    w = make_window()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert winq_process_alive(child.pid)
        live = w._autosave_dir / f"session-{child.pid}.winq"
        dead = w._autosave_dir / "session-999999.winq"
        w._autosave_dir.mkdir(parents=True, exist_ok=True)
        live.write_text("{}", encoding="utf-8")
        dead.write_text("{}", encoding="utf-8")
        assert w.leftover_autosaves() == [dead]
    finally:
        child.kill()
        child.wait()


class _FakeEngine:
    """Just enough of AudioEngine for the output watchdog."""

    is_open = True
    callbacks = 0
    samplerate = 48000
    device_name = "Test"

    class mixer:  # noqa: N801
        @staticmethod
        def take_peaks() -> tuple[float, float]:
            return 0.0, 0.0

    @staticmethod
    def poll_events() -> list:
        return []


def test_output_watchdog_warns_when_callbacks_stop_and_clears_when_they_resume(make_window):
    w = make_window()
    w.engine = _FakeEngine()  # type: ignore[assignment]
    w._tick()
    assert w.transport_bar.alert == ""
    w._callbacks_moved_at = time.monotonic() - 5  # no callback for 5 s
    w._tick()
    assert w.transport_bar.alert == OUTPUT_LOST
    assert w.transport_bar.warning.text() == OUTPUT_LOST
    w.engine.callbacks += 1  # the device came back
    w._tick()
    assert w.transport_bar.alert == ""
    w.engine = None


def test_alert_returns_after_a_brief_warning(make_window, qtbot):
    w = make_window()
    bar = w.transport_bar
    bar.set_alert("lasting")
    bar.show_warning("brief", seconds=0.05)
    assert bar.warning.text() == "brief"
    qtbot.waitUntil(lambda: bar.warning.text() == "lasting", timeout=2000)


def test_inspector_can_be_hidden_and_is_remembered(make_window, tmp_path):
    w = make_window(tmp_path)
    w.set_inspector_visible(False)
    assert w.inspector.isHidden()
    w2 = make_window(tmp_path)
    assert w2.inspector.isHidden()
    assert not w2._inspector_action.isChecked()


def test_headers_line_up_with_their_columns(make_window):
    m = make_window().cue_model
    role = Qt.ItemDataRole.TextAlignmentRole
    left = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    assert m.headerData(Col.NAME, Qt.Orientation.Horizontal, role) == left
    assert m.headerData(Col.DURATION, Qt.Orientation.Horizontal, role) == right


def test_errors_are_logged_and_reported_without_a_dialog(make_window, tmp_path, monkeypatch):
    from winq import crash_log

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(crash_log, "_listeners", [])
    import threading

    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    try:
        folder = crash_log.install("test")
        w = make_window()
        crash_log.on_error(w.error_logged.emit)
        try:
            raise ValueError("kaboom")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        assert "kaboom" in w.statusBar().currentMessage()
        for h in list(crash_log.log.handlers):
            h.flush()
        assert "kaboom" in (folder / "winq.log").read_text(encoding="utf-8")
    finally:
        sys.excepthook, threading.excepthook = old_hook, old_thread_hook
        for h in list(crash_log.log.handlers):
            crash_log.log.removeHandler(h)
            h.close()
        import faulthandler

        faulthandler.disable()
        if crash_log._fault_file is not None:
            crash_log._fault_file.close()
            crash_log._fault_file = None
