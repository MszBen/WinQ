# WinQ — Development plan

The behaviour spec, stack and layout are in [../CLAUDE.md](../CLAUDE.md). This file
only covers order of work. Each phase ends with something runnable and tested.

## Status
- Phase 0: **done** (2026-11-24)
- Phase 1: **done** (2026-11-24). Engine verified on real hardware: WASAPI, 48 kHz, ~22 ms latency, 0 xruns.
- Phase 2: **done** (2026-11-24). Cue list, .winq files, drag-drop add/reorder, delete, undo/redo.
- Phase 3: **done** (2026-11-24). GO/Esc/Shift+Esc, background decoding, Active Cues panel, play-state column. Checked on real hardware: 0 xruns.
- Phase 4: **done** (2026-11-24). Inspector (Basics + Levels), live cue levels, master fader saved in the show, stereo peak meter.
- Phase 5: **done** (2026-11-25). Waveform trim/fade editor with zoom, typed times, repeats / loop forever, Stop fade, Preview, live playheads.
- Phase 6: **done** (2026-11-25). Auto-follow/continue (sample-accurate), Show mode (Ctrl+M), Preferences, relink, recent shows, reopen last, autosave + recovery, PyInstaller build, README.
- Release 0.9.0 beta: **done** (2026-11-26). Launch hardening (engine never dies on an error, output-lost warning, error log, safe autosave recovery), Help/View menus, faster waveform painting, slimmer build, Inno Setup installer (`tools/build_release.ps1`).

## Phase 0 — Scaffold
- `pyproject.toml` (src layout, deps, `[dev]` extras, ruff + pytest config, `winq` entry point).
- `src/winq/__main__.py` opens an empty dark main window.
- Smoke test: the package imports, and `sounddevice` lists devices.

## Phase 1 — Audio engine (no UI)
- `loader.py`: decode with soundfile, upmix mono to stereo, resample with soxr to the device rate, compute peaks.
- `voice.py`: position, trim region, repeat counter / infinite flag, devamp flag, fade-in (first pass), end fade (final pass), stop fade, smoothed gain.
- `engine.py`: one OutputStream, command queue, mixing, master gain, limiter, event queue back to the GUI.
- Tests: render voices offline and assert sample-exact loop boundaries, fade shapes, gain ramps and devamp behaviour.

## Phase 2 — Cue model, show files, cue list UI
- `AudioCue`, `CueList`, `.winq` load/save with relative paths and missing-file detection.
- Main window with the cue table. Add cues via drag-drop from Explorer or File → Add. Reorder by drag. Delete.
- Undo/redo for all edits.

## Phase 3 — Transport
- `transport.py` implementing the GO / Esc / Shift+Esc rules in CLAUDE.md §1, with the start-order queue.
- GO button, standing-by label, app-wide shortcuts that are ignored while typing in text fields.
- Active Cues panel (Esc order, remaining time, per-row stop).
- Exhaustive `test_transport.py`, covering every rule and edge case in §1.

## Phase 4 — Levels
- Inspector Levels tab: dB slider/spinbox, applied live to playing instances.
- Master fader and a simple output peak meter.

## Phase 5 — Time & Loops
- Waveform widget: draggable Start/End, fade-in and end-fade handles, playhead for running instances.
- Repeats control (0…N or Infinite) and the Stop fade field.
- ∞ badge and the "loop ended" state in the cue table.

## Phase 6 — Show mode, preferences, polish, packaging
- Edit/Show mode toggle that locks all edits.
- Preferences: output device, panic fade, (later) buffer size.
- Relink missing files. Remember the last show. Crash-safe autosave.
- PyInstaller build and a README "how to use".

## Open questions
None right now. Ask the owner before starting anything new.

## Possible future work (not agreed; ask first)
- Stream long files from disk instead of decoding them fully into RAM (only needed for
  very large shows).
- Per-cue output device or channel routing.
- Code-signing the exe and installer (removes the SmartScreen "unknown publisher" warning; needs a certificate).
- A guard against two WinQ instances sharing one autosave folder.
