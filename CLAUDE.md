# CLAUDE.md — WinQ

WinQ is a Windows, Python, **audio-only** take on QLab-style cue playback for live shows
(theatre, events). No lighting, video or network control. The roadmap lives in
[docs/PLAN.md](docs/PLAN.md).

**Working rule from the project owner: if anything is unclear, ask rather than assume.**
That goes for behaviour, UI and dependency choices. Record each answer under
"Decisions log" below.

---

## 1. Behaviour spec (source of truth)

### Cues and the cue list
- A show is one ordered **cue list** of **audio cues**. Each cue points at one audio file.
- Supported formats: WAV, MP3, FLAC, OGG, AIFF (all decoded by `soundfile`). M4A/AAC is
  deliberately out of scope for now.
- Exactly one cue is **selected** (the "standing by" cue). Space acts on it.
- Any number of cues, and several copies of the same cue, can play at the same time.

### Per-cue settings
| Setting | Meaning |
|---|---|
| Number, Name, Notes | Free text. The number is a label, not an index. |
| File | Path to the audio file. Missing files show in red with a *Relink…* action. |
| Level (dB) | -inf to +12 dB. Changing it **while the cue plays takes effect at once** (smoothed, no zipper noise). |
| Start / End | Non-destructive trim points. The source file is never modified. |
| Fade in | Applied from Start on the **first pass only**. |
| End fade | Fades out just before End, on the **final pass only**. Drawn on the waveform. |
| Stop fade | Fade time used when **Escape** stops this cue. Separate from End fade. |
| Repeats | `0` = play once; `N` = play N extra times (1 = "repeat once" = 2 passes); or **Infinite**. The loop region is Start→End, unless a repeat section is set. |
| Repeat section | *Repeat only a section* (off by default): repeats play only Loop start→Loop end (blue lines on the waveform; Loop end = None follows End). **First play**: *whole track* Start→End (default) or *intro* Start→Loop end. **After last repeat**: *stop at Loop end* (default) or *play on* Loop end→End (only after a pass that ended at Loop end). Space on a looping section cue finishes the current pass, then stops or plays on. Fade in: first play only. End fade: the very last segment. Jumps are sample-accurate. The table shows `↻ 2 §` / `∞ §`. |
| Loop fades | Fade in / Fade out at the loop points of a repeating cue (blue ramps + blue square handles at the bottom of the waveform). Editable with a repeat section, or on any repeating cue when the *Display* preference allows it (or the cue already has some). Modes: **Dip at every jump back** (default: fade out before each jump to Loop start, including before End after a whole first play; fade in after it), **Dip at Loop end only** (the jump from End only fades in), **Fade every pass** (also dips into the outro and at the final Loop end), **Crossfade** (no dip: the pass's tail blends with the audio just before Loop start; length = the Fade-out field, limited by the audio before Loop start; pass lengths don't change). A dip or crossfade already under way always completes: ending the loop inside a dip lets it finish (an outro fades back in); inside a crossfade the jump happens and the next pass is the last. Ignored when Repeats = 0. |

### Keyboard / transport
- **Space = GO**, acting on the selected cue S:
  1. If S is an **Infinite** cue and an instance of it is playing and still looping:
     **end the loop** (devamp). That instance finishes its current pass and stops. If
     several instances are looping, end the oldest one. Selection moves to the next cue.
     **Nothing new starts.**
  2. Otherwise, **start a new instance** of S. If S is already playing, a second
     overlapping copy starts.
     - If S is Infinite, **selection stays on S**, so the next Space ends its loop.
     - Otherwise selection moves to the next cue.
  - Repeats with a fixed count are never cut short by Space. All passes always play.
  - Space at the end of the list (no next cue) leaves the selection on the last cue.
  - Space on a cue whose file is missing or unreadable plays nothing, shows a warning
    and moves the selection on.
  - The selection can always be moved freely (arrows or clicking), even away from a
    looping cue. To end that loop later, select it again and press Space, or use its
    stop button in Active Cues.
  - Holding a key down never re-triggers it (auto-repeat is ignored).
  - **Double-GO guard** (Preferences, default 0.3 s, can be Off): a GO within that
    time of the last accepted GO is ignored, with a brief warning in the GO bar.
- **Escape = stop oldest**: fades out the running instance that was **started
  earliest**, using its Stop fade. Instances that are already stopping are skipped, so
  pressing Esc twice quickly fades the two oldest. Instances that end on their own leave
  the queue.
- **Shift+Esc = panic**: stops everything, using the global panic fade from Preferences.
  It also cancels every pending automatic start and stops a running preview.
- Up/Down arrows move the selection. Clicking a row selects it.
- Space and Esc work wherever focus is, except while typing in a text field.

### Time & Loops editor and Preview
- A waveform shows the file. Drag the green Start line, the red End line and the two
  amber fade handles (squares at the top). Exact values can be typed (m:ss.cc).
  Dragging End to the file's end means "play to the end of the file" (`end_time = None`).
- Ctrl+wheel zooms around the cursor, wheel or drag pans, double-click fits.
- Click the waveform to set the dashed **preview point**. **Preview** plays the cue
  from there (level, fades and repeats as normal). It is **not** in the Esc queue and
  not shown in Active Cues. Press it again to stop it. Selecting another cue stops
  it, and Shift+Esc stops it too.
- Live playheads (white) show every playing copy of the selected cue; the preview
  playhead is cyan.
- **Live edits**: every Time & Loops setting (Start/End, fade in, end fade, repeats /
  Loop forever, repeat section on/off, Loop start/end, First play, After last, loop
  fades) changes **every playing and waiting copy of the cue, and the preview, at
  once**, like Level. Each copy keeps its phase and pass and recomputes its current
  segment from the new values:
  - playhead now past the segment's end → it acts as if it just reached it: jumps back
    to Loop start, plays on, or ends (a natural end, so a follow still fires);
  - playhead now before the segment's start → it jumps forward to it;
  - every such jump gets a 5 ms crossfade (de-click);
  - fades are recomputed from where the playhead is (an edit during a running fade can
    change its level);
  - raising/lowering Repeats or ticking Loop forever applies to the copy's remaining
    passes; a copy made Infinite waits for Space, unless its loop was already ended.
  Stop fade is read when Esc is pressed. Then/Delay apply to the next GO.
- Pressing Return in a single-line field commits it and gives focus back to the
  cue list, so the next Space is GO.

### Auto-follow / auto-continue ("Then" setting, per cue)
- `continue_mode` says what this cue does to the **next cue in the list**:
  - `none`: nothing; the next cue waits for Space.
  - `follow`: the next cue starts when this one **ends naturally** (after all repeats;
    for a loop-forever cue, after its loop is ended and the last pass finishes), plus
    `continue_delay` seconds.
  - `continue`: the next cue starts `continue_delay` seconds after this one **starts**.
- Chains are allowed (A→B→C…). GO schedules the **whole chain in the engine at once**,
  so a follow handoff is **sample-accurate** (no gap, no overlap).
- **Escape or Stop All cancels a pending follow** (only a natural end triggers it). For
  `continue`, stopping the first cue before its delay runs out cancels the waiting cue
  too.
- **Selection skips past the chain**: after Space on A (which follows into B), the
  selection goes to C. If the chain contains a loop-forever cue, the selection stops
  on it so the next Space ends its loop.
- Pending (waiting) cues are listed in Active Cues as *waiting* (⧗ in the cue list).
  Esc skips them. Their own stop button cancels them (and whatever waits on them).
  When one starts, it joins the end of the Esc order.
- Space on a cue that is pending starts it **now**, and its own chain stays attached.
- If a cue in the chain has a missing file, the chain stops there, with a warning.

### Show mode
- **Ctrl+M**, the EDIT/SHOW button in the GO bar, or *Show → Show Mode*.
- Show mode locks editing: no adding, deleting, reordering, inline edits, inspector
  changes, preview, undo/redo, relink or Preferences. Selection, GO, Escape, Stop All,
  the master fader and saving still work. The GO bar gets an amber border and the title
  shows `[SHOW MODE]`. The mode isn't saved; WinQ starts in Edit mode unless
  *Open shows in Show mode* is on (then every show opened from a file enters it).
- While in Show mode, Windows is kept from sleeping or turning the screen off.
- **Quitting** while anything plays, or while in Show mode, asks first (default
  button: Keep running). This is standard behaviour, not a preference.

### Files, recovery, preferences
- On launch WinQ reopens the last show. A `.winq` path on the command line wins.
  *File → Open Recent* keeps 8 entries.
- Autosave: every 30 s by default (Off / 30 s / 1 min / 5 min in Preferences), if
  there are unsaved changes, a backup goes to
  `%APPDATA%/WinQ/WinQ/autosave/session-<pid>.winq`. It never touches the user's `.winq`.
  The backup is deleted on save, new/open and normal close. If a backup from another
  session is found on launch, WinQ offers to recover it (Yes: it opens as the original
  show, still unsaved; No: it's discarded).
- *Edit → Relink File…* (Ctrl+L, also in the cue's right-click menu) points a cue at
  another file. Other missing cues whose file name exists in the new folder can be
  relinked in the same undo step.
- Preferences (QSettings): the output device is stored as `"<host API>|<device name>"`,
  because PortAudio indices change between launches. If that device isn't found, WinQ
  falls back to the default device with a status-bar note. Changing the device stops
  everything.
- Other preferences (`ui/app_settings.py`, keys `prefs/…`; the panic fade keeps its
  old `panic_fade` key), in tabs:
  - **Audio:** output device; buffer Low/Normal/Safe (PortAudio latency "low" /
    50 ms / 150 ms; changing it reopens the device and stops everything); *Play test
    tone* (440 Hz, -18 dBFS, left then right, on the output in use; not a cue, Stop
    All stops it).
  - **Show:** Stop All fade; double-GO guard; open shows in Show mode.
  - **New cues:** level, fade in, end fade, Stop fade, Then. Apply to cues added
    afterwards; fades that don't fit the file shrink in proportion.
  - **Display:** UI size 100/115/130 % (`QT_SCALE_FACTOR`, next launch); the standby
    cue's notes in the GO bar (on; looping/missing hints take priority); Active Cues
    shows time remaining or elapsed; *Loop fades also for whole-track repeats* (off).
  - **Files:** reopen the last show on launch (on); autosave interval.

### Output
- One output device, chosen in Preferences (via WASAPI / PortAudio), in stereo.
- **Master fader** on the main window, applied after the mix. Range -inf to +6 dB.
  It is **saved in the show file** (`master_db`). Changing it is undoable and marks
  the show unsaved. It works in Show mode too.
- A stereo peak meter sits next to the master fader. Click it to reset the clip light.

---

## 2. Tech stack

Python **3.12** (64-bit). Python 3.14 is also installed on the dev machine. Don't use it
until PySide6, sounddevice and soxr all publish wheels for it.

| Concern | Library | Why |
|---|---|---|
| GUI | **PySide6** (Qt 6, LGPL) | Native-feeling desktop widgets, model/view tables, QUndoStack, good HiDPI. The LGPL licence is friendlier than PyQt6's GPL. |
| Audio output | **sounddevice** (PortAudio) | Low-latency callback stream with device selection (WASAPI/MME/DirectSound). We mix every cue ourselves in **one** stream, so overlap, trims, loops and live gain are sample-accurate. |
| Decoding | **soundfile** (libsndfile ≥ 1.1) | WAV/FLAC/OGG/AIFF **and MP3** in one wheel with no FFmpeg. |
| Resampling | **soxr** (python-soxr) | High-quality conversion to the device sample rate at load time. |
| DSP / buffers | **numpy** | Vectorised mixing in the callback. |
| Persistence | stdlib `json` + `dataclasses` | Show files are small. No ORM or pydantic needed. |
| Tests | **pytest**, **pytest-qt** | The engine is tested offline by calling its render function directly. |
| Lint/format | **ruff** | One tool for both. |
| Packaging | **PyInstaller** | Builds a single-folder Windows `.exe`. |

Rejected options (don't reintroduce them without asking):
- `pygame.mixer`: fixed channel count, no sample-accurate trim or loop control, poor live gain.
- `python-vlc` / `QMediaPlayer`: an external runtime or a gap at loop points, and no sample-accurate trims.
- `pydub`: needs FFmpeg, and it isn't a playback engine.
- `simpleaudio`/`playsound`: unmaintained or too limited.

---

## 3. Architecture

```
GUI thread (Qt)                         Audio thread (PortAudio callback)
───────────────                         ─────────────────────────────────
Transport ──commands──► SimpleQueue ──► AudioEngine.callback()
   ▲                                       mixes Voices → master gain → out
   └──── QTimer (~30 Hz) polls ◄── events queue (voice started / ended / position)
```

- **AudioEngine** (`audio/engine.py`) owns one `sounddevice.OutputStream`. The GUI never
  touches voices directly. It sends commands (`Play`, `SetGain`, `Devamp`, `Stop(fade)`,
  `StopAll`, `SetMaster`, `Reshape` for live Time & Loops edits) through a queue.
- **Voice** (`audio/voice.py`) is one playing *instance* of a cue. It holds the buffer
  reference, frame position, loop state, a smoothed gain and the fade envelope.
  `render(frames) -> ndarray` is pure, so tests can call it.
- **CuePlayer / AudioCache** (`audio/cue_player.py`) turn cues (seconds, dB) into
  Voices (frames, linear gain), and keep decoded audio by path. Files decode on a
  2-thread pool when cues are added or a show is opened.
- **Loader** (`audio/loader.py`) decodes a whole file to float32 stereo at the device
  rate, kept in RAM, and computes waveform peaks. It runs in a worker thread when a cue
  is added or a show is opened. RAM cost is about 23 MB per minute at 48 kHz stereo,
  which is fine for typical shows. Streaming from disk is a later option. Ask before
  switching.
- **Transport** (`control/transport.py`) holds all the GO/Esc/panic rules from §1 and
  keeps the **start-order queue** of running instances. It is plain Python with no Qt
  and no audio, so the rules can be unit-tested.
- **Model** (`model/`) has the `AudioCue` dataclass, `CueList` and show load/save.
  The UI binds to these through Qt models.

### Audio-callback rules (important)
- No file I/O, no locks that the GUI can hold for long, no Python object allocation
  churn, no logging and no Qt calls inside `callback()`.
- Drain the command queue at the start of each callback with `get_nowait` until it is empty.
- Every gain change is ramped (≥ 5 ms) to avoid clicks. Even a "hard" stop gets a
  ~5 ms de-click ramp.
- Clip or soft-limit the final mix to [-1, 1].
- An exception must never escape the callback (PortAudio would stop the stream). A voice
  whose render raises is dropped as if stopped; anything else outputs one silent block.

---

## 4. Repo layout

```
WinQ/
├── CLAUDE.md
├── README.md
├── pyproject.toml            # deps, ruff, pytest config; entry point `winq`
├── winq.spec                 # PyInstaller build (icon, version info, slimmed Qt)
├── LICENSE.txt               # proprietary licence (shown by the installer)
├── THIRD_PARTY_NOTICES.txt   # open-source components; full texts in licenses/
├── licenses/                 # copied next to WinQ.exe by winq.spec
├── installer/winq.iss        # Inno Setup 6 script → dist/installer/WinQ-<ver>-Setup.exe
├── assets/winq.ico           # exe icon, generated by tools/make_icon.py from ui/winq.svg
├── tools/make_icon.py
├── tools/build_release.ps1   # tests → exe → smoke test → installer
├── docs/
│   └── PLAN.md               # phased roadmap
├── src/winq/
│   ├── __main__.py           # `python -m winq`
│   ├── app.py                # QApplication bootstrap, theme
│   ├── keep_awake.py         # stop Windows sleeping in Show mode
│   ├── crash_log.py          # unhandled errors → %APPDATA%/WinQ/WinQ/logs, keep running
│   ├── win_process.py        # is another WinQ process alive? (autosave recovery)
│   ├── model/
│   │   ├── cue.py            # AudioCue dataclass
│   │   ├── cue_list.py       # ordering, selection
│   │   └── show_file.py      # .winq JSON load/save, path relativisation
│   ├── audio/
│   │   ├── engine.py         # stream + command queue + mixer
│   │   ├── voice.py          # one playing instance
│   │   ├── loader.py         # decode, resample, peaks
│   │   ├── cue_player.py     # cue → Voice, decoded-audio cache, progress
│   │   ├── devices.py        # list/choose output devices
│   │   ├── dsp.py            # dB↔gain, ramps, fade curves
│   │   └── test_tone.py      # left/right speaker check (Preferences → Audio)
│   ├── control/
│   │   └── transport.py      # GO / Esc / panic rules, start-order queue
│   └── ui/
│       ├── main_window.py
│       ├── commands.py       # QUndoCommands for every edit
│       ├── level_control.py  # dB slider+spinbox, stereo peak meter
│       ├── time_loops.py     # Time & Loops tab (fields, repeats, preview)
│       ├── time_edit.py      # m:ss.cc spin box
│       ├── cue_table.py      # QTableView + QAbstractTableModel
│       ├── active_cues.py    # running instances in Esc order
│       ├── inspector.py      # Basics / Time & Loops / Levels tabs
│       ├── waveform.py       # QPainter waveform with trim + fade handles
│       ├── transport_bar.py  # GO button, standby label, master fader, mode toggle
│       ├── single_instance.py  # one WinQ per user; later launches hand over their show
│       ├── elided_label.py   # QLabel that shortens long text with "…"
│       ├── prefs_dialog.py   # tabbed Preferences dialog
│       ├── app_settings.py   # AppPrefs dataclass ↔ QSettings, UI scale
│       ├── theme.qss         # dark theme
│       ├── tokens.py         # palette + numeric font for painted widgets (mirrors theme.qss)
│       └── winq.svg          # logo (window/taskbar icon, rendered by app.load_icon)
└── tests/
    ├── test_voice.py
    ├── test_transport.py
    ├── test_engine_render.py
    └── test_show_file.py
```

---

## 5. UI layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ [ GO ]  Standing by: 3 · "Storm ambience" (∞)    [Edit|Show]  Master ▮│
├──────────────────────────────────────────────┬───────────────────────┤
│ ▶ #  Name               Dur    Level  Rep    │ ACTIVE (Esc order)    │
│ ● 1  Pre-show music     3:12   -6 dB  –      │ 1. Pre-show  1:02 ■   │
│ ▶ 2  Door slam          0:02    0 dB  –      │ 2. Storm ∞   0:41 ■   │
│ ∞ 3  Storm ambience     0:45   -3 dB  ∞      │                       │
│   4  Thunder            0:06   +2 dB  ×2     │                       │
├──────────────────────────────────────────────┴───────────────────────┤
│ Inspector: [Basics] [Time & Loops] [Levels]                          │
│  ▁▂▅▇▆▃▂▁▂▅▇█▇▅▃▂  ← drag Start/End, fade handles; repeats; stop fade │
└──────────────────────────────────────────────────────────────────────┘
```
- Dark theme for dim show environments. Large GO button. The selected row is strongly
  highlighted.
- Playing rows show a progress fill. Infinite-looping rows show a ∞ badge that changes
  once the loop has been ended.
- The **Active Cues** panel lists running instances in start order, which is the Escape
  order. Each row has a remaining-time readout and its own stop button.
- Drag audio files from Explorer into the list to create cues. Drag rows to reorder them.
- Ctrl+Z/Ctrl+Y undo/redo every edit (QUndoStack). Ctrl+N/O/S/Shift+S for show files.

### Show file (`.winq`)
JSON with a `"format_version"` field. Paths are stored **relative to the show file**
when possible, otherwise absolute. Times are stored in seconds (float) and levels in dB.

---

## 6. Commands

Tests never open the real audio device or touch the real settings/autosave folder.
Use the `make_window` fixture
(tests/conftest.py) and drive sound with `w.engine.mixer.render(n)` then
`w._tick()`. Don't register MainWindows with `qtbot.addWidget`: pytest-qt closes them
before fixture teardown, and the unsaved-changes dialog then hangs the run.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m winq            # run (optionally: python -m winq show.winq)
python -m winq --smoke-test   # opens the real device ~1.5 s; exit 0 = OK
pytest                    # tests
ruff check . ; ruff format .
pyinstaller winq.spec --noconfirm   # → dist\WinQ\WinQ.exe (then run it with --smoke-test)
powershell -ExecutionPolicy Bypass -File tools\build_release.ps1   # full release + installer
```

---

## 7. Conventions
- Type hints everywhere. Use `from __future__ import annotations`.
- Keep Qt out of `model/`, `audio/` and `control/`, so they stay unit-testable without a display.
- Time is in **frames** inside the engine and in **seconds** in the model/UI. Convert at the boundary.
- Gain is linear inside the engine and in dB in the model/UI.
- Any change to §1 behaviour gets a test in `tests/test_transport.py` or `test_voice.py`.
- Every cue edit goes through `EditCueCommand` (undoable). Continuous gestures (a
  slider drag, one typing session in Notes) pass a `merge_key` so they undo as one
  step. `CueTableModel.cue_attr_changed` fires after any edit/undo/redo; that is
  where live effects (level on playing copies) are applied.
- Level sliders never take keyboard focus, so arrow keys always move the cue
  selection during a show.
- **Never use `setButtonSymbols(NoButtons)`.** With our stylesheet it collapses the
  spin box's text to 1 px, so the value is invisible. Spin arrows are hidden in
  theme.qss instead (`tests/test_theme_regressions.py` checks this).
- Styles inside the inspector need a `QTabWidget#inspector …` prefix to beat its
  blanket `QWidget` background rule.
- **No horizontal scrolling, laptop first** (`tests/test_layout.py`): the window's minimum
  width must stay ≤ 1024 px. Text that can be long (cue names, paths, device names) goes in
  an `ElidedLabel`, never a plain `QLabel`. Scroll areas turn the horizontal bar off.

---

## 8. Decisions log
| Date | Decision |
|---|---|
| 2026-11-24 | Audio-only. No lighting, video or network. |
| 2026-11-24 | Space on a playing Infinite cue only ends its loop (finish pass). The next cue starts on the following Space. While looping, the selection stays on the looping cue. |
| 2026-11-24 | Space ends the loop of the **selected** cue only, not every loop. |
| 2026-11-24 | Fixed-count repeats always play in full. Space doesn't shorten them. |
| 2026-11-24 | Esc stops the oldest running instance with that cue's own Stop fade. Shift+Esc = panic. |
| 2026-11-24 | Trim and fades are non-destructive and edited on the waveform. End fade and Stop fade are separate settings. |
| 2026-11-24 | Space on an already-playing non-loop cue starts a second overlapping copy. |
| 2026-11-24 | One output device chosen in prefs. Live per-cue level changes. Master fader. |
| 2026-11-24 | Formats: WAV/MP3/FLAC/OGG/AIFF only (no M4A/AAC). |
| 2026-11-24 | Edit/Show mode lock is wanted. |
| 2026-11-24 | Stack: PySide6 + sounddevice + soundfile + soxr + numpy, Python 3.12. |
| 2026-11-24 | The default output is Windows' default device opened via **WASAPI** at the device's own sample rate. Files are resampled to that rate on load. |
| 2026-11-24 | Every fade (fade in, end fade, stop fade) uses an S-curve (raised cosine). Gain changes glide over 10 ms. "Instant" stops get a 5 ms de-click. |
| 2026-11-24 | If a loop is ended while already inside the end-fade zone, the fade is shortened to start immediately. The pass still ends on time, and the level doesn't jump. |
| 2026-11-24 | Mono files play on both sides. Files with more than 2 channels use the first two. |
| 2026-11-24 | GO on a playing Infinite cue that has several looping instances ends the oldest one. |
| 2026-11-24 | New-cue defaults: level 0 dB, no fade-in, no end fade, Stop fade 1 s. Panic fade 0.5 s. |
| 2026-11-24 | New cues are auto-numbered (next whole number after the highest) and named after the file (no extension). Dropped folders are expanded one level. |
| 2026-11-24 | The cue list is single-selection (one standing-by cue). In-table editing covers Number and Name only; the other fields are edited in the inspector (Phases 4–5). |
| 2026-11-24 | The selection moves freely even while a cue loops (no lock, no reminder banner). |
| 2026-11-24 | Space on a missing or unreadable cue skips it with a warning and advances. |
| 2026-11-24 | Space/Esc are caught by an app-wide event filter for keys aimed at the main window. Text fields, spin boxes and dialogs are excluded. |
| 2026-11-24 | Master level is saved per show file, with a range of -inf to +6 dB. Master changes are undoable and mark the show unsaved. |
| 2026-11-24 | The inspector has Basics (number, name, file, notes) and Levels. Time & Loops comes in Phase 5. Levels go to -60 dB = -inf (stored as -60). |
| 2026-11-25 | Preview is wanted: it plays from a point you click on the waveform, sits outside GO/Esc, and the same button stops it. |
| 2026-11-25 | The waveform zooms with Ctrl+wheel, pans with wheel or drag, and double-click fits the whole file. Exact times can also be typed. |
| 2026-11-25 | Trim and fade edits don't change copies already playing (only level is live). |
| 2026-11-25 | Show mode toggle is Ctrl+M. |
| 2026-11-25 | Autosave writes a separate backup and offers recovery after a crash. |
| 2026-11-25 | On launch, WinQ reopens the last show. There's also an Open Recent menu. |
| 2026-11-25 | Auto-follow is in scope with both modes: start the next cue when this one ends (+ delay), or N s after this one starts. |
| 2026-11-25 | Esc or Stop All on a cue cancels its pending follow. Stop All cancels all pending starts. The selection skips past the chain. |
| 2026-11-25 | Confirmed by owner: Stopping a cue also cancels its 'continue' child if that child is still waiting. The selection stops on a loop-forever cue inside a chain. |
| 2026-11-25 | Logo chosen by the owner: Q ring with an amber play triangle on a green rounded square. Used for the window, taskbar and exe icon. |
| 2026-11-25 | Theme restyled by owner request: "blue-lit booth". Graded navy surfaces; colour only means state (lamp green, sodium amber, stop red, cyan); indigo standing-by row. GO is a flat matte green (#2a8f4c, no glow or gradient: owner prefers matte). Bahnschrift with tabular figures for GO, standby readout, numbers and times; Segoe UI for reading. |
| 2026-11-25 | Laptop-first layout: the window never scrolls sideways. Long names and paths shorten with an ellipsis (full text in the tooltip); the cue table fits its columns to the width; inspector tabs scroll vertically only. The window opens sized to the screen. |
| 2026-11-26 | Ask before quitting while cues play or in Show mode, and keep the PC awake in Show mode. Both are always on. |
| 2026-11-26 | Preferences gain: buffer size, test tone, double-GO guard (default 0.3 s), open in Show mode, new-cue defaults, UI size, notes in the GO bar, elapsed/remaining time, reopen-last toggle, autosave interval. |
| 2026-11-26 | Repeat section: repeats can loop only part of a cue. First play whole track (default) or intro into the section; after the last repeat stop at Loop end (default) or play on to End. Both are per-cue settings, set with a checkbox + blue waveform handles. Like trims, edits apply to the next start. |
| 2026-11-26 | Live edits: everything in Time & Loops (trims, fades, repeats, loop settings) changes all playing copies and the preview at once. A loop/segment end moved before the playhead jumps back (or ends) now, with a 5 ms de-click. Replaces "trim and fade edits apply to the next start". |
| 2026-11-26 | Loop fades (fade in/out at the loop points), per cue. Default mode dips at every jump back; "Loop end only", "every pass" and "crossfade" are per-cue alternatives. Default: only with a repeat section; a Display preference allows them on whole-track repeats. |
| 2026-11-26 | Installer: **Inno Setup 6** (chosen by Claude at the owner's request: free, modern wizard, used by VS Code; one script). Per-user install, no admin (`%LOCALAPPDATA%\Programs\WinQ`). Registers `.winq`, Start menu entry, optional desktop shortcut (unticked). Uninstall keeps settings/autosaves. |
| 2026-11-26 | First public build is **0.9.0 (beta)**. Version lives in `src/winq/__init__.py` (exe file properties and installer read it). |
| 2026-11-26 | Launch hardening: a failing voice is dropped, never the stream; the GO bar keeps a red warning while the output device has stopped (unplugged); unexpected errors go to a log (*Help → Open Log Folder*) and a status-bar note, never a modal dialog mid-show; autosave recovery skips backups of WinQ windows that are still open. |
| 2026-11-26 | UX additions: *View → Inspector* (Ctrl+E, remembered) to give the cue list the whole window; *Help* menu (Keyboard Shortcuts F1, Open Log Folder, About); Show menu labels Space/Esc/Shift+Esc; cue-list headers align with their columns. |
| 2026-11-26 | The build drops Qt parts WinQ never loads (QML/Quick, PDF, network, software OpenGL, Qt translations): 156 → 107 MB; installer ~29 MB. |
| 2026-11-26 | One WinQ per Windows user (owner approved). A second launch sends its `.winq` to the running WinQ over a local socket (QtNetwork is bundled again for this) and exits; the running window comes forward and opens it after the usual save prompt. |
| 2026-11-26 | Licence: **proprietary, all rights reserved** (owner's choice): free to install and use, no copying/modifying/redistributing the source. Holder: "Benedek Miko-Szabo and other contributors". LICENSE.txt is shown by the installer; THIRD_PARTY_NOTICES.txt + licenses/ cover LGPL Qt, libsndfile, soxr etc. Qt stays as replaceable DLLs (LGPL). Only the non-ASIO 64-bit PortAudio DLL is shipped (the ASIO SDK has its own terms). |
