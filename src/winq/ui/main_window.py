"""Main window: menus, cue list, transport (GO/Esc/panic), Show mode, preferences,
show files (recent list, autosave recovery)."""

from __future__ import annotations

import contextlib
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSettings, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QKeyEvent,
    QKeySequence,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QFileDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from winq import __version__
from winq.audio.cue_player import AudioCache, CuePlayer, Progress, level_to_gain
from winq.audio.devices import list_output_devices
from winq.audio.engine import AudioEngine, VoiceCancelled, VoiceEnded, VoiceStarted
from winq.audio.loader import SUPPORTED_EXTENSIONS, probe_duration
from winq.audio.test_tone import tone_voice
from winq.control.transport import GoAction, Transport
from winq.crash_log import log_dir
from winq.keep_awake import set_keep_awake
from winq.model.cue import AudioCue
from winq.model.show_file import (
    SHOW_EXTENSION,
    ShowFileError,
    load_show,
    read_autosave_meta,
    save_show,
)
from winq.ui.active_cues import ActiveCuesPanel
from winq.ui.app_settings import AppPrefs, load_prefs, save_prefs
from winq.ui.commands import (
    AddCuesCommand,
    EditCueCommand,
    MarkerCommand,
    MoveCueCommand,
    RemoveCueCommand,
    SetMasterCommand,
)
from winq.ui.cue_table import CueTableModel, CueTableView, supported_audio_files
from winq.ui.elided_label import ElidedLabel
from winq.ui.inspector import Inspector
from winq.ui.prefs_dialog import PreferencesDialog, device_key
from winq.ui.transport_bar import TransportBar
from winq.win_process import pid_from_autosave_name, winq_process_alive

AUDIO_FILTER = "Audio files ({})".format(" ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS)))
SHOW_FILTER = f"WinQ show (*{SHOW_EXTENSION})"
TICK_MS = 30  # GUI refresh / engine event polling interval
PREVIEW_STOP_FADE = 0.05  # seconds
FALLBACK_SAMPLERATE = 48000
DEFAULT_SIZE = (1280, 800)  # logical px; shrunk to fit smaller screens
TITLE_BAR = 32  # room left for the Windows title bar and frame
WINDOW_FRAME = 8
INSPECTOR_HEIGHT = 400  # room for the Time & Loops fields under a usable waveform
# Cue settings that change copies already playing (live edits; level has its own path).
LIVE_SHAPE_ATTRS = frozenset(
    {
        "start_time", "end_time", "fade_in", "end_fade", "repeats", "loop_section",
        "loop_start", "loop_end", "loop_first", "loop_after", "loop_fade_in",
        "loop_fade_out", "loop_fade_mode",
    }
)  # fmt: skip
TEXT_ENTRY_WIDGETS = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
MAX_RECENT = 8
COPYRIGHT = "© 2026 Benedek Miko-Szabo and other contributors"


def legal_dir() -> Path:
    """Where LICENSE.txt lives: next to WinQ.exe when built, the repo root from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


OUTPUT_STALL_SECONDS = 1.5  # no audio callback for this long = the output has stopped
OUTPUT_LOST = "Audio output stopped (device unplugged or busy?). Choose it again in Preferences."
AUTOSAVE_PREFIX = "session-"
SHORTCUTS_HTML = """<table cellspacing="0" cellpadding="3">
<tr><td colspan="2"><b>Running a show</b></td></tr>
<tr><td>Space</td><td>GO (start the standing-by cue; on a looping cue, end its loop)</td></tr>
<tr><td>Esc</td><td>Fade out the oldest playing cue</td></tr>
<tr><td>Shift+Esc</td><td>Stop All (also cancels automatic starts)</td></tr>
<tr><td>↑ / ↓</td><td>Move the selection</td></tr>
<tr><td>Ctrl+M</td><td>Edit / Show mode</td></tr>
<tr><td colspan="2"><br><b>Editing</b></td></tr>
<tr><td>Ctrl+I</td><td>Add audio files</td></tr>
<tr><td>Delete</td><td>Delete the selected cue</td></tr>
<tr><td>Ctrl+L</td><td>Relink a missing file</td></tr>
<tr><td>Ctrl+Z / Ctrl+Y</td><td>Undo / Redo</td></tr>
<tr><td>Return</td><td>Commit a field and go back to the cue list</td></tr>
<tr><td>Ctrl+E</td><td>Show / hide the inspector</td></tr>
<tr><td colspan="2"><br><b>Waveform</b></td></tr>
<tr><td>Ctrl+wheel</td><td>Zoom</td></tr>
<tr><td>Wheel / drag</td><td>Pan</td></tr>
<tr><td>Double-click</td><td>Fit the whole file</td></tr>
<tr><td>Click</td><td>Set the preview point</td></tr>
<tr><td colspan="2"><br><b>Files</b></td></tr>
<tr><td>Ctrl+N / Ctrl+O</td><td>New / Open show</td></tr>
<tr><td>Ctrl+S / Ctrl+Shift+S</td><td>Save / Save As</td></tr>
<tr><td>Ctrl+,</td><td>Preferences</td></tr>
</table>"""


class _NoAudioPlayer:
    """Stand-in when no output device could be opened: nothing ever starts."""

    def start(self, cue: AudioCue, **_: object) -> int | None:
        return None

    def start_now(self, voice_id: int) -> None: ...
    def cancel(self, voice_id: int) -> None: ...
    def devamp(self, voice_id: int) -> None: ...
    def stop(self, voice_id: int, fade_seconds: float) -> None: ...
    def stop_all(self, fade_seconds: float) -> None: ...
    def set_level(self, voice_id: int, level_db: float) -> None: ...
    def forget(self, voice_id: int) -> None: ...

    def progress(self, voice_id: int) -> Progress | None:
        return None

    def preview(self, cue: AudioCue, from_seconds: float) -> int | None:
        return None

    def position(self, voice_id: int) -> float | None:
        return None


def make_cues(
    paths: list[Path], first_number: int, prefs: AppPrefs | None = None
) -> list[AudioCue]:
    """New cues for these files: auto-numbered, named after the file, duration probed,
    with the new-cue defaults from Preferences (fades shortened to fit the file)."""
    prefs = prefs if prefs is not None else AppPrefs()
    cues = []
    for i, path in enumerate(paths):
        cue = AudioCue(
            file_path=path.resolve(),
            number=str(first_number + i),
            name=path.stem,
            level_db=prefs.cue_level_db,
            stop_fade=prefs.cue_stop_fade,
            continue_mode=prefs.cue_then,
        )
        cue.file_duration = probe_duration(cue.file_path)
        fade_in, end_fade = prefs.cue_fade_in, prefs.cue_end_fade
        if cue.file_duration is not None and fade_in + end_fade > cue.file_duration:
            scale = cue.file_duration / (fade_in + end_fade)
            fade_in, end_fade = round(fade_in * scale, 3), round(end_fade * scale, 3)
        cue.fade_in, cue.end_fade = fade_in, end_fade
        cues.append(cue)
    return cues


def default_autosave_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base or Path.home() / ".winq") / "autosave"


class _HeightFollower(QObject):
    """Keeps `follower` exactly as tall as `source` (the Active Cues title follows the cue
    list header, so the lines under them meet)."""

    def __init__(self, source: QWidget, follower: QWidget) -> None:
        super().__init__(source)
        self._follower = follower
        source.installEventFilter(self)
        follower.setFixedHeight(source.sizeHint().height())

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize and obj.height() != self._follower.height():
            self._follower.setFixedHeight(obj.height())
        return False


class MainWindow(QMainWindow):
    error_logged = Signal(str)  # from any thread (crash_log); shown in the status bar

    def __init__(
        self,
        *,
        start_audio: bool = True,
        settings: QSettings | None = None,
        autosave_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self._fit_to_screen()
        self.settings = settings if settings is not None else QSettings("WinQ", "WinQ")
        self.show_path: Path | None = None
        self.prefs = load_prefs(self.settings)
        self.master_db = 0.0
        self.show_mode = False
        self.confirm_quit = True  # tests turn this off; see closeEvent
        self._last_go = float("-inf")  # time.monotonic() of the last accepted GO
        self._test_tone: int | None = None
        self._preview_voice: int | None = None
        self._preview_cue_id: str | None = None
        self._pending_wave: Path | None = None  # waveform waiting for its decode
        self._seen_callbacks = -1  # engine.callbacks at the last tick (output watchdog)
        self._callbacks_moved_at = time.monotonic()
        self._output_lost = False
        self._autosave_dir = autosave_dir if autosave_dir is not None else default_autosave_dir()
        self._autosave_file = self._autosave_dir / f"{AUTOSAVE_PREFIX}{os.getpid()}{SHOW_EXTENSION}"

        device_index, device_note = self._saved_device_index()
        audio_error = self._init_audio(start_audio, device_index)

        self.undo_stack = QUndoStack(self)
        self.undo_stack.cleanChanged.connect(self._update_title)

        self.cue_model = CueTableModel(self.undo_stack, self)
        self.cue_model.files_dropped.connect(self.add_files)
        self.cue_model.move_requested.connect(self._move_cue)
        self.cue_model.cue_attr_changed.connect(self._cue_attr_changed)
        self.cue_view = CueTableView()
        self.cue_view.setModel(self.cue_model)
        self.cue_view.selectionModel().selectionChanged.connect(self._selection_changed)
        self.cue_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cue_view.customContextMenuRequested.connect(self._cue_context_menu)

        self.transport_bar = TransportBar()
        self.transport_bar.show_notes = self.prefs.show_notes
        self.transport_bar.go_clicked.connect(self.go)
        self.transport_bar.panic_clicked.connect(self.panic)
        self.transport_bar.master.level_changed.connect(self._master_fader_moved)
        self.transport_bar.mode_toggled.connect(self.set_show_mode)
        self.active_panel = ActiveCuesPanel()
        self.active_panel.time_mode = self.prefs.active_time
        self.active_panel.stop_requested.connect(self._stop_voice)

        self.inspector = Inspector()
        self.inspector.time_loops.loop_fades_whole = self.prefs.loop_fades_whole
        self.inspector.edit_requested.connect(self.edit_cue)
        self.inspector.edit_group_requested.connect(self.edit_cue_group)
        self.inspector.preview_requested.connect(self.start_preview)
        self.inspector.preview_stop_requested.connect(self.stop_preview)

        # The Active Cues title is as tall as the cue list header, so their lines meet.
        self._header_follower = _HeightFollower(
            self.cue_view.horizontalHeader(), self.active_panel.title
        )

        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.addWidget(self.cue_view)
        top_split.addWidget(self.active_panel)
        top_split.setSizes([850, 350])

        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(self.inspector)
        # The inspector gets the height its fields need (at most half the window); the cue
        # list takes the rest and absorbs window resizes.
        inspector_h = min(INSPECTOR_HEIGHT, self.height() // 2)
        main_split.setSizes([max(1, self.height() - inspector_h), inspector_h])
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 0)
        top_split.setStretchFactor(0, 1)
        top_split.setStretchFactor(1, 0)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.transport_bar)
        layout.addWidget(main_split, 1)
        self.setCentralWidget(central)

        self.output_label = ElidedLabel(self._output_description())
        self.statusBar().addPermanentWidget(self.output_label)

        self._build_menus()
        self.error_logged.connect(self._report_error)
        if self.settings.value("view/inspector", True, type=bool) is False:
            self.set_inspector_visible(False)
        self._update_title()
        self._selection_changed()
        self.cue_view.setFocus()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.autosave)
        self._apply_autosave_interval()
        QApplication.instance().installEventFilter(self)
        if audio_error:
            QTimer.singleShot(0, lambda: QMessageBox.warning(self, "Audio output", audio_error))
        elif device_note:
            self.statusBar().showMessage(device_note, 8000)

    @property
    def panic_fade(self) -> float:
        return self.prefs.panic_fade

    # ----------------------------------------------------------------- window size
    def _fit_to_screen(self) -> None:
        """Start at a comfortable size, but never bigger than the screen's free area
        (laptops: 1280x720 at 150 %, 1280x800 at 200 % ...), centred on it."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(DEFAULT_SIZE[0], DEFAULT_SIZE[1])
            return
        free = screen.availableGeometry()
        width = min(DEFAULT_SIZE[0], free.width() - 2 * WINDOW_FRAME)
        height = min(DEFAULT_SIZE[1], free.height() - TITLE_BAR - WINDOW_FRAME)
        self.resize(width, height)
        self.move(
            free.x() + (free.width() - width) // 2,
            free.y() + (free.height() - height - TITLE_BAR) // 2,
        )

    # ----------------------------------------------------------------- audio setup
    def _saved_device_index(self) -> tuple[int | None, str]:
        """The output device chosen in Preferences, found again by name. Falls back to
        the default device (with a note) if it isn't connected."""
        key = self.settings.value("audio/device", "")
        if not key:
            return None, ""
        try:
            devices = list_output_devices()
        except Exception:
            return None, ""
        for d in devices:
            if device_key(d) == key:
                return d.index, ""
        return None, f"Saved output device not found ({key.split('|')[-1]}); using the default."

    def _init_audio(self, start: bool, device_index: int | None = None) -> str | None:
        """Create engine, cache, player and transport. Returns an error message or None."""
        error = None
        self.engine: AudioEngine | None = None
        try:
            self.engine = AudioEngine(device_index, latency=self.prefs.latency)
            if start:
                self.engine.start()
        except Exception as exc:  # no device, device busy, driver error...
            self.engine = None
            error = f"Could not open the audio output, so cues will not play.\n\n{exc}"
        samplerate = self.engine.samplerate if self.engine else FALLBACK_SAMPLERATE
        self.cache = AudioCache(samplerate)
        self.player: CuePlayer | _NoAudioPlayer = (
            CuePlayer(self.engine, self.cache) if self.engine else _NoAudioPlayer()
        )
        self.transport = Transport(self.player)
        return error

    def _output_description(self) -> str:
        if self.engine is None:
            return "Output: none"
        return f"Output: {self.engine.device_name} · {self.engine.samplerate} Hz"

    def switch_output_device(self, device_index: int | None, *, start: bool = True) -> bool:
        """Re-open audio on another device. Everything playing is stopped. Returns True
        if the new device opened."""
        if self.engine is not None:
            self.engine.close()
        self.cache.shutdown()
        self._preview_voice = self._preview_cue_id = None
        self._test_tone = None
        error = self._init_audio(start, device_index)
        self._seen_callbacks = -1
        self._callbacks_moved_at = time.monotonic()
        if self._output_lost:
            self._output_lost = False
            self.transport_bar.set_alert("")
        self.set_master_db(self.master_db)
        self.cache.prefetch([c.file_path for c in self.cue_model.cue_list if not c.is_missing])
        self.output_label.setText(self._output_description())
        self.inspector.time_loops.set_preview_playing(False)
        self._transport_changed()
        self._load_inspector_audio()
        if error:
            QMessageBox.warning(self, "Audio output", error)
            return False
        return True

    # ----------------------------------------------------------------- transport
    def go(self) -> None:
        now = time.monotonic()
        guard = self.prefs.go_guard
        if guard > 0 and now - self._last_go < guard:
            self.transport_bar.show_warning(
                f"Second GO ignored (double-GO guard: {guard:g} s)", seconds=2.0
            )
            return
        self._last_go = now
        selected = self.cue_view.selected_row()
        result = self.transport.go(self.cue_model.cue_list.cues, selected)
        if result.action is GoAction.SKIPPED and result.cue is not None:
            self.transport_bar.show_warning(
                f"Skipped {result.cue.number} · {result.cue.name}: file missing or unreadable"
            )
        elif result.chain_broken_at is not None:
            c = result.chain_broken_at
            self.transport_bar.show_warning(
                f"Auto-start stopped at {c.number} · {c.name}: file missing or unreadable"
            )
        if result.selection is not None and result.selection != selected:
            self.cue_view.select_row(result.selection)
        self._transport_changed()

    def stop_oldest(self) -> None:
        self.transport.stop_oldest()
        self._transport_changed()

    def panic(self) -> None:
        """Stop everything (incl. preview) and cancel every pending automatic start."""
        self.transport.panic(self.panic_fade)
        self._transport_changed()

    def _stop_voice(self, voice_id: int) -> None:
        self.transport.stop_voice(voice_id)
        self._transport_changed()

    def _tick(self) -> None:
        changed = False
        if self.engine is not None:
            for event in self.engine.poll_events():
                if isinstance(event, VoiceStarted):
                    changed |= self.transport.voice_started(event.voice_id) is not None
                    continue
                # VoiceEnded or VoiceCancelled
                self.player.forget(event.voice_id)
                if event.voice_id == self._preview_voice:
                    self._preview_ended()
                    continue
                if event.voice_id == self._test_tone:
                    self._test_tone = None
                    continue
                if isinstance(event, VoiceEnded | VoiceCancelled):
                    self.transport.voice_ended(event.voice_id)
                    changed = True
            self.transport_bar.meter.push(self.engine.mixer.take_peaks(), TICK_MS / 1000)
            self._watch_output()
        if changed:
            self._transport_changed()
        else:
            self.active_panel.refresh(self.transport.running, self.player.progress)
        if self._pending_wave is not None and self.cache.is_ready(self._pending_wave):
            self._load_inspector_audio()
        self._update_playheads()

    def _watch_output(self) -> None:
        """Warn (and keep warning) if the audio device stops asking for audio, e.g. a USB
        interface was unplugged mid-show. The warning clears by itself if it comes back."""
        engine = self.engine
        if engine is None or not engine.is_open:
            return
        now = time.monotonic()
        if engine.callbacks != self._seen_callbacks:
            self._seen_callbacks = engine.callbacks
            self._callbacks_moved_at = now
        lost = now - self._callbacks_moved_at > OUTPUT_STALL_SECONDS
        if lost != self._output_lost:
            self._output_lost = lost
            self.transport_bar.set_alert(OUTPUT_LOST if lost else "")
            self.output_label.setText("Output: LOST" if lost else self._output_description())

    def _update_playheads(self) -> None:
        cue = self.inspector.cue
        if cue is None:
            return
        heads = [
            self.player.position(r.voice_id)
            for r in self.transport.instances_of(cue.id)
            if not r.pending
        ]
        preview = None
        if self._preview_voice is not None and self._preview_cue_id == cue.id:
            preview = self.player.position(self._preview_voice)
        self.inspector.time_loops.waveform.set_playheads(
            [h for h in heads if h is not None], preview
        )

    def _transport_changed(self) -> None:
        rank = {"looping": 4, "playing": 3, "waiting": 2, "stopping": 1}
        states: dict[str, str] = {}
        for r in self.transport.running:
            if r.pending:
                state = "stopping" if r.stopping else "waiting"
            else:
                state = "looping" if r.looping else "stopping" if r.stopping else "playing"
            if rank[state] > rank.get(states.get(r.cue.id, ""), 0):
                states[r.cue.id] = state
        self.cue_model.set_play_states(states)
        self.active_panel.refresh(self.transport.running, self.player.progress)
        self._update_standby()

    def _selection_changed(self, *_: object) -> None:
        row = self.cue_view.selected_row()
        cue = None if row is None else self.cue_model.cue_at(row)
        if cue is not self.inspector.cue:
            self.stop_preview()  # the preview belongs to the cue being edited
        self.inspector.set_cue(cue)
        self._load_inspector_audio()
        self._update_standby()

    def _load_inspector_audio(self) -> None:
        """Give the waveform its audio, or a message while it decodes."""
        self._pending_wave = None
        cue = self.inspector.cue
        if cue is None:
            self.inspector.set_audio(None, "No cue selected")
            return
        if cue.is_missing:
            self.inspector.set_audio(None, "File missing or unreadable (Edit → Relink File…)")
            return
        audio = self.cache.peek(cue.file_path)
        if audio is not None:
            self.inspector.set_audio(audio)
        elif self.cache.is_ready(cue.file_path):  # decode finished but failed
            self.inspector.set_audio(None, "Could not decode this file")
        else:
            self.cache.prefetch([cue.file_path])
            self._pending_wave = cue.file_path
            self.inspector.set_audio(None, "Loading waveform…")

    # ----------------------------------------------------------------- preview
    def start_preview(self, cue_id: str, from_seconds: float) -> None:
        """Audition a cue from a point. Not part of GO/Esc; Shift+Esc still stops it."""
        self.stop_preview()
        row = self.cue_model.cue_list.index_of(cue_id)
        if row is None or self.show_mode:
            return
        voice_id = self.player.preview(self.cue_model.cue_at(row), from_seconds)
        if voice_id is None:
            self.transport_bar.show_warning("Preview failed: file missing or unreadable")
            self.inspector.time_loops.set_preview_playing(False)
            return
        self._preview_voice, self._preview_cue_id = voice_id, cue_id
        self.inspector.time_loops.set_preview_playing(True)

    def stop_preview(self) -> None:
        if self._preview_voice is not None:
            self.player.stop(self._preview_voice, PREVIEW_STOP_FADE)
        self._preview_ended()

    def _preview_ended(self) -> None:
        self._preview_voice = None
        self._preview_cue_id = None
        self.inspector.time_loops.set_preview_playing(False)

    def _update_standby(self, *_: object) -> None:
        row = self.cue_view.selected_row()
        cue = None if row is None else self.cue_model.cue_at(row)
        looping = cue is not None and self.transport.looping_instance(cue.id) is not None
        self.transport_bar.set_standby(cue, looping=looping)

    # ----------------------------------------------------------------- Show mode
    def toggle_show_mode(self) -> None:
        self.set_show_mode(not self.show_mode)

    def set_show_mode(self, on: bool) -> None:
        """Show mode locks every edit. GO/Esc/Stop All, selection, the master fader and
        saving still work."""
        self.show_mode = on
        set_keep_awake(on)  # no sleep or screen-off mid-show
        if on:
            self.stop_preview()
            if self.cue_view.state() == QAbstractItemView.State.EditingState:
                self.cue_view.setFocus()  # closes an open inline editor
        self.cue_model.editable = not on
        mode = QAbstractItemView.DragDropMode
        self.cue_view.setDragDropMode(mode.NoDragDrop if on else mode.DragDrop)
        self.cue_view.setAcceptDrops(not on)
        self.inspector.set_locked(on)
        for action in self._edit_actions:
            action.setEnabled(not on)
        self._show_mode_action.setChecked(on)
        self.transport_bar.set_show_mode(on)
        self.transport_bar.setProperty("showMode", on)
        self.transport_bar.style().unpolish(self.transport_bar)
        self.transport_bar.style().polish(self.transport_bar)
        self._update_title()
        self.cue_view.setFocus()

    # ----------------------------------------------------------------- keyboard
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Space / Esc / Shift+Esc work wherever focus is in this window, except while
        typing in a text field. Held-down keys never re-trigger."""
        if event.type() != QEvent.Type.KeyPress:
            return False
        # Only keys aimed at a widget in this window (not dialogs, not other windows).
        if not isinstance(obj, QWidget) or obj.window() is not self:
            return False
        assert isinstance(event, QKeyEvent)
        key, mods = event.key(), event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._in_single_line_field():
            # Let the field commit first, then give focus back to the cue list so the
            # next Space is GO, not a character in the field.
            QTimer.singleShot(0, self.cue_view.setFocus)
            return False
        if key not in (Qt.Key.Key_Space, Qt.Key.Key_Escape) or self._is_typing():
            return False
        if event.isAutoRepeat():
            return True
        no_mods = mods == Qt.KeyboardModifier.NoModifier
        if key == Qt.Key.Key_Space and no_mods:
            self.go()
        elif key == Qt.Key.Key_Escape and no_mods:
            self.stop_oldest()
        elif key == Qt.Key.Key_Escape and mods == Qt.KeyboardModifier.ShiftModifier:
            self.panic()
        else:
            return False
        return True

    def _in_single_line_field(self) -> bool:
        w = QApplication.focusWidget()
        is_field = isinstance(w, QLineEdit | QAbstractSpinBox)
        in_table_editor = w is not None and self.cue_view.isAncestorOf(w)
        return is_field and not in_table_editor

    @staticmethod
    def _is_typing() -> bool:
        return isinstance(QApplication.focusWidget(), TEXT_ENTRY_WIDGETS)

    # ----------------------------------------------------------------- menus
    def _action(self, text: str, slot, shortcut=None) -> QAction:  # noqa: ANN001
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        file_menu.addAction(self._action("&New Show", self.new_show, QKeySequence.StandardKey.New))
        file_menu.addAction(
            self._action("&Open Show…", self.open_show, QKeySequence.StandardKey.Open)
        )
        self.recent_menu = QMenu("Open &Recent", self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addMenu(self.recent_menu)
        file_menu.addAction(self._action("&Save", self.save_show, QKeySequence.StandardKey.Save))
        file_menu.addAction(
            self._action("Save &As…", self.save_show_as, QKeySequence("Ctrl+Shift+S"))
        )
        file_menu.addSeparator()
        add_files = self._action("&Add Audio Files…", self.add_files_dialog, QKeySequence("Ctrl+I"))
        file_menu.addAction(add_files)
        file_menu.addSeparator()
        prefs = self._action("&Preferences…", self.open_preferences, QKeySequence("Ctrl+,"))
        file_menu.addAction(prefs)
        file_menu.addSeparator()
        file_menu.addAction(self._action("E&xit", self.close, QKeySequence("Alt+F4")))

        edit_menu = bar.addMenu("&Edit")
        undo = self.undo_stack.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        redo = self.undo_stack.createRedoAction(self, "&Redo")
        redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.addAction(undo)
        self.addAction(redo)
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)
        edit_menu.addSeparator()
        delete = self._action(
            "&Delete Cue", self.delete_selected_cue, QKeySequence.StandardKey.Delete
        )
        edit_menu.addAction(delete)
        self.relink_action = self._action(
            "Re&link File…", self.relink_selected_cue, QKeySequence("Ctrl+L")
        )
        edit_menu.addAction(self.relink_action)

        show_menu = bar.addMenu("&Show")
        self._show_mode_action = self._action(
            "&Show Mode (lock editing)", self.toggle_show_mode, QKeySequence("Ctrl+M")
        )
        self._show_mode_action.setCheckable(True)
        show_menu.addAction(self._show_mode_action)
        show_menu.addSeparator()
        # Space/Esc are handled by the event filter (so they never fire while typing);
        # the text after the tab only labels them in the menu.
        show_menu.addAction(self._action("&GO	Space", self.go))
        show_menu.addAction(self._action("Stop &Oldest	Esc", self.stop_oldest))
        show_menu.addAction(self._action("Stop &All	Shift+Esc", self.panic))

        view_menu = bar.addMenu("&View")
        self._inspector_action = self._action(
            "&Inspector", self.set_inspector_visible, QKeySequence("Ctrl+E")
        )
        self._inspector_action.setCheckable(True)
        self._inspector_action.setChecked(True)
        view_menu.addAction(self._inspector_action)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(
            self._action("&Keyboard Shortcuts", self.show_shortcuts, QKeySequence("F1"))
        )
        help_menu.addAction(self._action("Open &Log Folder", self.open_log_folder))
        help_menu.addSeparator()
        help_menu.addAction(self._action("&Licences", self.open_licences))
        help_menu.addAction(self._action("&About WinQ", self.show_about))

        # Disabled in Show mode. (Undo/redo too: undoing an edit mid-show is an edit.)
        self._edit_actions = [add_files, prefs, undo, redo, delete, self.relink_action]

    # ----------------------------------------------------------------- other launches
    def activate_from_other_launch(self, show: str = "") -> None:
        """WinQ was launched again (e.g. a .winq was double-clicked): come to the front
        and open that show, asking to save unsaved changes first."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if not show:
            return
        path = Path(show)
        if self.show_path is not None and path.resolve() == self.show_path.resolve():
            return  # already open
        if QApplication.activeModalWidget() is not None:
            self.statusBar().showMessage(
                f"Close the open dialog, then open {path.name} again.", 8000
            )
            return
        if self._maybe_save():
            self.load_show_file(path)

    # ----------------------------------------------------------------- view / help
    def set_inspector_visible(self, visible: bool) -> None:
        """Hide the inspector to give the cue list the whole window (small screens,
        running a show). Remembered between sessions."""
        self.inspector.setVisible(visible)
        self._inspector_action.setChecked(visible)
        self.settings.setValue("view/inspector", visible)
        if not visible:
            self.stop_preview()
            self.cue_view.setFocus()

    def show_shortcuts(self) -> None:
        QMessageBox.information(self, "Keyboard shortcuts", SHORTCUTS_HTML)

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About WinQ",
            f"<h3>WinQ {__version__}</h3>"
            "<p>Audio cue playback for live shows.</p>"
            f"<p>{COPYRIGHT}. All rights reserved.</p>"
            "<p>Built with Qt / PySide6 (LGPL), PortAudio, libsndfile, soxr and NumPy. "
            "See Help → Licences for the terms of WinQ and these components.</p>"
            f"<p>Error log: {log_dir()}</p>",
        )

    def open_licences(self) -> None:
        """Show the folder with LICENSE.txt, THIRD_PARTY_NOTICES.txt and licenses/."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(legal_dir())))

    def open_log_folder(self) -> None:
        folder = log_dir()
        with contextlib.suppress(OSError):
            folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _report_error(self, summary: str) -> None:
        """An unexpected error was logged (crash_log). WinQ keeps running; tell the
        operator without a dialog that could steal Space mid-show."""
        self.statusBar().showMessage(
            f"Internal error (WinQ keeps running; see Help → Open Log Folder): {summary}",
            15000,
        )

    def _cue_context_menu(self, pos) -> None:  # noqa: ANN001
        if self.show_mode or self.cue_view.indexAt(pos).row() < 0:
            return
        menu = QMenu(self)
        menu.addAction(self.relink_action)
        menu.addAction("Delete Cue", self.delete_selected_cue)
        menu.exec(self.cue_view.viewport().mapToGlobal(pos))

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.recent_shows()
        for path in recent:
            action = self.recent_menu.addAction(str(path))
            action.triggered.connect(lambda _=False, p=path: self._open_recent(p))
            action.setEnabled(path.exists())
        if not recent:
            self.recent_menu.addAction("(none)").setEnabled(False)
        else:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear List", self._clear_recent)

    def _open_recent(self, path: Path) -> None:
        if self._maybe_save():
            self.load_show_file(path)

    # ----------------------------------------------------------------- preferences
    def open_preferences(self) -> None:
        if self.show_mode:
            return
        try:
            devices = list_output_devices()
        except Exception as exc:
            QMessageBox.warning(self, "Preferences", f"Could not list audio devices:\n{exc}")
            return
        current = self.engine.device_index if self.engine else None
        dialog = PreferencesDialog(devices, current, self.prefs, self)
        dialog.test_tone_requested.connect(self.play_test_tone)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = dialog.selected_prefs()
        chosen = dialog.selected_device()
        device_changed = chosen is not None and chosen != current
        reopen = device_changed or new.buffer != self.prefs.buffer
        if reopen and (self.transport.running or self._preview_voice is not None):
            answer = QMessageBox.question(
                self,
                "Change audio output",
                "Cues are playing. Changing the output device or buffer stops everything. "
                "Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                new.buffer = self.prefs.buffer  # keep everything else, skip the reopen
                reopen = device_changed = False
        self.apply_prefs(new)
        if not reopen:
            return
        opened = self.switch_output_device(chosen if device_changed else current)
        if opened and device_changed:
            d = next(d for d in devices if d.index == chosen)
            self.settings.setValue("audio/device", device_key(d))

    def apply_prefs(self, prefs: AppPrefs) -> None:
        """Save preferences and apply the ones that take effect at once. (Buffer and
        device need switch_output_device; UI size needs a restart.)"""
        self.prefs = prefs
        save_prefs(self.settings, prefs)
        self.active_panel.time_mode = prefs.active_time
        self.transport_bar.show_notes = prefs.show_notes
        self.inspector.time_loops.loop_fades_whole = prefs.loop_fades_whole
        self.inspector.time_loops.set_cue(self.inspector.time_loops.cue)
        self._update_standby()
        self._apply_autosave_interval()

    def _apply_autosave_interval(self) -> None:
        if self.prefs.autosave_seconds > 0:
            self._autosave_timer.start(self.prefs.autosave_seconds * 1000)
        else:
            self._autosave_timer.stop()

    def play_test_tone(self) -> None:
        """A short tone on the left, then the right speaker. Not a cue; Stop All stops it."""
        if self.engine is None:
            QMessageBox.warning(self, "Test tone", "No audio output is open.")
            return
        if self._test_tone is not None:
            self.engine.stop(self._test_tone, PREVIEW_STOP_FADE)
        voice = tone_voice(self.engine.next_voice_id(), self.engine.samplerate)
        self._test_tone = voice.voice_id
        self.engine.play(voice)

    # ----------------------------------------------------------------- cue editing
    def add_files(self, paths: list[Path], row: int | None = None) -> None:
        if self.show_mode:
            return
        ok, rejected = supported_audio_files([Path(p) for p in paths])
        if rejected:
            names = ", ".join(p.name for p in rejected[:3]) + ("…" if len(rejected) > 3 else "")
            self.statusBar().showMessage(f"Skipped unsupported files: {names}", 6000)
        if not ok:
            return
        if row is None:
            selected = self.cue_view.selected_row()
            row = self.cue_model.rowCount() if selected is None else selected + 1
        cues = make_cues(ok, self.cue_model.cue_list.next_number(), self.prefs)
        self.undo_stack.push(AddCuesCommand(self.cue_model, row, cues))
        self.cue_view.select_row(row)
        self.cache.prefetch([c.file_path for c in cues if not c.is_missing])
        missing = [c.file_path.name for c in cues if c.is_missing]
        if missing:
            self.statusBar().showMessage(f"Could not read: {', '.join(missing)}", 6000)

    def add_files_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add Audio Files", self._last_dir("audio"), AUDIO_FILTER
        )
        if files:
            self._remember_dir("audio", Path(files[0]).parent)
            self.add_files([Path(f) for f in files])

    def delete_selected_cue(self) -> None:
        row = self.cue_view.selected_row()
        if row is None or self.show_mode:
            return
        self.undo_stack.push(RemoveCueCommand(self.cue_model, row))
        self.cue_view.select_row(row)

    def edit_cue(
        self, cue_id: str, attr: str, value: object, label: str, merge_key: object = None
    ) -> None:
        """Undoable change of one cue attribute (from the inspector)."""
        row = self.cue_model.cue_list.index_of(cue_id)
        if row is None or self.show_mode:
            return
        old = getattr(self.cue_model.cue_at(row), attr)
        if old == value:
            return
        self.undo_stack.push(
            EditCueCommand(self.cue_model, cue_id, attr, old, value, label, merge_key)
        )

    def edit_cue_group(self, cue_id: str, changes: list, label: str) -> None:
        """Several attribute changes on one cue as a single undo step."""
        if self.show_mode or self.cue_model.cue_list.index_of(cue_id) is None:
            return
        self.undo_stack.beginMacro(f"Change {label}")
        for attr, value in changes:
            self.edit_cue(cue_id, attr, value, label)
        self.undo_stack.endMacro()

    def _cue_attr_changed(self, cue_id: str, attr: str) -> None:
        """After any edit, undo or redo: push live changes to playing copies of the cue,
        and keep the inspector in sync."""
        row = self.cue_model.cue_list.index_of(cue_id)
        cue = self.cue_model.cue_at(row) if row is not None else None
        if attr == "level_db" and cue is not None:
            for r in self.transport.instances_of(cue_id):
                self.player.set_level(r.voice_id, cue.level_db)
            if self._preview_voice is not None and self._preview_cue_id == cue_id:
                self.player.set_level(self._preview_voice, cue.level_db)
        if attr in LIVE_SHAPE_ATTRS and cue is not None:
            for r in self.transport.instances_of(cue_id):
                self.player.reshape(r.voice_id, cue)
            if self._preview_voice is not None and self._preview_cue_id == cue_id:
                self.player.reshape(self._preview_voice, cue)
            if attr == "repeats":
                self.transport.repeats_changed(cue_id)
                self._update_standby()
        if attr == "file_path" and cue is not None:
            cue.file_duration = probe_duration(cue.file_path)
            if not cue.is_missing:
                self.cache.prefetch([cue.file_path])
            self.cue_model.set_cue_attr(cue_id, "file_duration", cue.file_duration)
            if self.inspector.cue is cue:
                self._load_inspector_audio()
        if self.inspector.cue is not None and self.inspector.cue.id == cue_id:
            self.inspector.set_cue(self.inspector.cue)
        if attr in ("number", "name", "file_path", "notes"):
            self._update_standby()

    # ----------------------------------------------------------------- relink
    def relink_selected_cue(self, new_path: Path | None = None, *, ask_others: bool = True) -> None:
        """Point the selected cue at another file. Other missing cues whose file name
        exists in the same new folder can be relinked in the same (undoable) step."""
        row = self.cue_view.selected_row()
        if row is None or self.show_mode:
            return
        cue = self.cue_model.cue_at(row)
        if new_path is None:
            start = (
                str(cue.file_path.parent)
                if cue.file_path.parent.exists()
                else self._last_dir("audio")
            )
            chosen, _ = QFileDialog.getOpenFileName(
                self, f"Relink “{cue.name}”", start, AUDIO_FILTER
            )
            if not chosen:
                return
            new_path = Path(chosen)
        new_path = new_path.resolve()
        folder = new_path.parent
        others = [
            c
            for c in self.cue_model.cue_list
            if c is not cue and c.is_missing and (folder / c.file_path.name).exists()
        ]
        if others and ask_others:
            answer = QMessageBox.question(
                self,
                "Relink other files",
                f"{len(others)} other missing file(s) were found in {folder}. Relink them too?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                others = []
        self.undo_stack.beginMacro("Relink file" + ("s" if others else ""))
        self.undo_stack.push(
            EditCueCommand(self.cue_model, cue.id, "file_path", cue.file_path, new_path, "file")
        )
        for c in others:
            self.undo_stack.push(
                EditCueCommand(
                    self.cue_model,
                    c.id,
                    "file_path",
                    c.file_path,
                    (folder / c.file_path.name).resolve(),
                    "file",
                )
            )
        self.undo_stack.endMacro()
        self._remember_dir("audio", folder)

    # ----------------------------------------------------------------- master level
    def _master_fader_moved(self, db: float, gesture: int) -> None:
        if db != self.master_db:
            self.undo_stack.push(
                SetMasterCommand(self.set_master_db, self.master_db, db, ("master", gesture))
            )

    def set_master_db(self, db: float) -> None:
        """Apply a master level to the engine and the fader (no undo entry)."""
        self.master_db = db
        self.transport_bar.master.set_level(db)
        if self.engine is not None:
            self.engine.set_master(level_to_gain(db))

    def _move_cue(self, src: int, final_index: int) -> None:
        self.undo_stack.push(MoveCueCommand(self.cue_model, src, final_index))
        self.cue_view.select_row(final_index)

    # ----------------------------------------------------------------- show files
    def startup(self, show_path: Path | None = None) -> None:
        """Called once after the window is shown: recover crashed sessions, otherwise
        open `show_path` (from the command line) or reopen the last show."""
        if self.offer_recovery():
            return
        if show_path is not None:
            self.load_show_file(show_path)
            return
        last = self.settings.value("last_show", "")
        if self.prefs.reopen_last and last and Path(last).exists():
            self.load_show_file(Path(last))

    def new_show(self) -> None:
        if not self._maybe_save():
            return
        self._set_show([], None, master_db=0.0)
        self.settings.setValue("last_show", "")

    def open_show(self) -> None:
        if not self._maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Show", self._last_dir("show"), SHOW_FILTER
        )
        if path:
            self.load_show_file(Path(path))

    def load_show_file(self, path: Path, *, as_recovery_of: Path | None = None) -> bool:
        try:
            show = load_show(path)
        except ShowFileError as exc:
            QMessageBox.critical(self, "Could not open show", str(exc))
            return False
        cues = show.cues
        for cue in cues:
            cue.file_duration = probe_duration(cue.file_path)
        if as_recovery_of is None:
            self._remember_dir("show", path.parent)
            self._set_show(cues, path, master_db=show.master_db)
            self._add_recent(path)
            if self.prefs.open_in_show_mode:
                self.set_show_mode(True)
        else:
            original = as_recovery_of if str(as_recovery_of) else None
            self._set_show(cues, original, master_db=show.master_db)
            self.undo_stack.push(MarkerCommand("Recovered from autosave"))
        missing = sum(c.is_missing for c in cues)
        if missing:
            self.statusBar().showMessage(
                f"{missing} cue file{'s are' if missing != 1 else ' is'} missing (shown in red;"
                " Edit → Relink File…)",
                8000,
            )
        return True

    def save_show(self) -> bool:
        if self.show_path is None:
            return self.save_show_as()
        return self._write_show(self.show_path)

    def save_show_as(self) -> bool:
        start = str(self.show_path) if self.show_path else self._last_dir("show")
        path, _ = QFileDialog.getSaveFileName(self, "Save Show As", start, SHOW_FILTER)
        if not path:
            return False
        p = Path(path)
        if p.suffix.lower() != SHOW_EXTENSION:
            p = p.with_name(p.name + SHOW_EXTENSION)
        self._remember_dir("show", p.parent)
        return self._write_show(p)

    def _write_show(self, path: Path) -> bool:
        try:
            save_show(path, self.cue_model.cue_list.cues, master_db=self.master_db)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save show", str(exc))
            return False
        self.show_path = path
        self.undo_stack.setClean()
        self._update_title()
        self._add_recent(path)
        self._remove_autosave()
        self.statusBar().showMessage(f"Saved {path.name}", 3000)
        return True

    def _set_show(self, cues: list[AudioCue], path: Path | None, master_db: float) -> None:
        playing = {r.cue.file_path for r in self.transport.running}
        self.cache.retain(playing | {c.file_path for c in cues})
        self.cache.prefetch([c.file_path for c in cues if not c.is_missing])
        self.cue_model.reset_cues(cues)
        self.show_path = path
        self.undo_stack.clear()
        self._remove_autosave()
        self.set_master_db(master_db)
        self._update_title()
        if cues:
            self.cue_view.select_row(0)
        self._selection_changed()

    def _maybe_save(self) -> bool:
        """Ask to save unsaved changes. Returns False if the user cancelled."""
        if self.undo_stack.isClean():
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to the current show?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_show()
        return answer == QMessageBox.StandardButton.Discard

    def _confirm_quit_during_show(self) -> bool:
        """Closing WinQ by accident mid-show is the worst case, so ask first while
        anything plays or Show mode is on. Defaults to No."""
        playing = bool(self.transport.running) or self._preview_voice is not None
        if not self.confirm_quit or not (playing or self.show_mode):
            return True
        reason = "Cues are playing" if playing else "WinQ is in Show mode"
        return self._ask_quit(
            f"{reason}. Quit anyway?" + (" Everything playing stops." if playing else "")
        )

    def _ask_quit(self, text: str) -> bool:
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            "Quit WinQ?",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("Quit")
        box.button(QMessageBox.StandardButton.No).setText("Keep running")
        box.exec()
        return box.clickedButton() is box.button(QMessageBox.StandardButton.Yes)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_quit_during_show() or not self._maybe_save():
            event.ignore()
            return
        event.accept()
        self._remove_autosave()
        self.shutdown()

    def shutdown(self) -> None:
        """Release audio and background threads. Safe to call more than once."""
        self._timer.stop()
        self._autosave_timer.stop()
        if self.show_mode:
            set_keep_awake(False)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self.engine is not None:
            self.engine.close()
        self.cache.shutdown()

    # ----------------------------------------------------------------- recent shows
    def recent_shows(self) -> list[Path]:
        raw = self.settings.value("recent_shows", []) or []
        if isinstance(raw, str):
            raw = [raw]
        return [Path(p) for p in raw if p]

    def _add_recent(self, path: Path) -> None:
        path = path.resolve()
        recent = [p for p in self.recent_shows() if p != path]
        recent.insert(0, path)
        self.settings.setValue("recent_shows", [str(p) for p in recent[:MAX_RECENT]])
        self.settings.setValue("last_show", str(path))

    def _clear_recent(self) -> None:
        self.settings.setValue("recent_shows", [])

    # ----------------------------------------------------------------- autosave
    def autosave(self) -> bool:
        """Write a backup of unsaved changes (never touches the .winq itself)."""
        if self.undo_stack.isClean():
            return False
        try:
            self._autosave_dir.mkdir(parents=True, exist_ok=True)
            save_show(
                self._autosave_file,
                self.cue_model.cue_list.cues,
                master_db=self.master_db,
                extra={
                    "autosave_of": str(self.show_path) if self.show_path else None,
                    "autosave_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
        except OSError:
            return False
        return True

    def _remove_autosave(self) -> None:
        with contextlib.suppress(OSError):
            self._autosave_file.unlink(missing_ok=True)

    def leftover_autosaves(self) -> list[Path]:
        """Backups left by sessions that didn't close normally (not this one)."""
        if not self._autosave_dir.is_dir():
            return []
        files = []
        for p in self._autosave_dir.glob(f"{AUTOSAVE_PREFIX}*{SHOW_EXTENSION}"):
            if p == self._autosave_file:
                continue
            pid = pid_from_autosave_name(p.stem, AUTOSAVE_PREFIX)
            if pid is not None and winq_process_alive(pid):
                continue  # another WinQ window is still open: that backup is its own
            files.append(p)
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def offer_recovery(self) -> bool:
        """Ask about each leftover backup (newest first). Returns True if one was
        recovered (it becomes the open, unsaved show)."""
        for backup in self.leftover_autosaves():
            try:
                original, saved_at = read_autosave_meta(backup)
            except ShowFileError:
                backup.unlink(missing_ok=True)
                continue
            name = original.name if original else "an untitled show"
            when = saved_at.replace("T", " ") if saved_at else "recently"
            answer = QMessageBox.question(
                self,
                "Recover unsaved changes?",
                f"WinQ didn't close properly last time.\n\n"
                f"Recover unsaved changes to {name} (autosaved {when})?\n\n"
                f"Choose No to discard the backup.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            recovered = answer == QMessageBox.StandardButton.Yes and self.load_show_file(
                backup, as_recovery_of=original or Path("")
            )
            backup.unlink(missing_ok=True)
            if recovered:
                self.autosave()  # keep it protected under this session's name
                return True
        return False

    # ----------------------------------------------------------------- helpers
    def _update_title(self, *_: object) -> None:
        name = self.show_path.stem if self.show_path else "Untitled"
        dirty = "" if self.undo_stack.isClean() else " •"
        mode = "  [SHOW MODE]" if self.show_mode else ""
        self.setWindowTitle(f"WinQ — {name}{dirty}{mode}")

    def _last_dir(self, kind: str) -> str:
        return str(self.settings.value(f"last_dir/{kind}", str(Path.home())))

    def _remember_dir(self, kind: str, directory: Path) -> None:
        self.settings.setValue(f"last_dir/{kind}", str(directory))
