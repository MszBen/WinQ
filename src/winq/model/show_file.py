"""Load and save `.winq` show files (JSON).

Audio paths are stored relative to the show file when possible (so a show folder can be
moved or copied to another machine), otherwise absolute. In memory they are absolute.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from winq.model.cue import (
    AFTER_STOP,
    CONTINUE_MODES,
    DEFAULT_LEVEL_DB,
    DEFAULT_STOP_FADE,
    FIRST_FULL,
    LOOP_AFTER_MODES,
    LOOP_FADE_JUMP,
    LOOP_FADE_MODES,
    LOOP_FIRST_MODES,
    NO_CONTINUE,
    AudioCue,
    new_cue_id,
)

FORMAT_VERSION = 1
SHOW_EXTENSION = ".winq"


class ShowFileError(Exception):
    pass


def _path_for_save(audio: Path, show_dir: Path) -> str:
    try:
        return Path(os.path.relpath(audio, show_dir)).as_posix()
    except ValueError:  # different drive on Windows
        return audio.as_posix()


def _path_from_save(stored: str, show_dir: Path) -> Path:
    p = Path(stored)
    return p if p.is_absolute() else (show_dir / p).resolve()


def cue_to_dict(cue: AudioCue, show_dir: Path) -> dict[str, Any]:
    return {
        "id": cue.id,
        "number": cue.number,
        "name": cue.name,
        "notes": cue.notes,
        "file": _path_for_save(cue.file_path, show_dir),
        "level_db": cue.level_db,
        "start_time": cue.start_time,
        "end_time": cue.end_time,
        "fade_in": cue.fade_in,
        "end_fade": cue.end_fade,
        "stop_fade": cue.stop_fade,
        "repeats": cue.repeats,
        "continue_mode": cue.continue_mode,
        "continue_delay": cue.continue_delay,
        "loop_section": cue.loop_section,
        "loop_start": cue.loop_start,
        "loop_end": cue.loop_end,
        "loop_first": cue.loop_first,
        "loop_after": cue.loop_after,
        "loop_fade_in": cue.loop_fade_in,
        "loop_fade_out": cue.loop_fade_out,
        "loop_fade_mode": cue.loop_fade_mode,
    }


def _continue_mode(value: object) -> str:
    """Unknown modes (e.g. from a newer WinQ) fall back to 'no automatic start'."""
    return value if value in CONTINUE_MODES else NO_CONTINUE


def cue_from_dict(d: dict[str, Any], show_dir: Path) -> AudioCue:
    try:
        file_path = _path_from_save(str(d["file"]), show_dir)
    except KeyError as exc:
        raise ShowFileError("A cue has no audio file") from exc
    end_time = d.get("end_time")
    loop_end = d.get("loop_end")
    return AudioCue(
        file_path=file_path,
        number=str(d.get("number", "")),
        name=str(d.get("name", "")),
        notes=str(d.get("notes", "")),
        level_db=float(d.get("level_db", DEFAULT_LEVEL_DB)),
        start_time=float(d.get("start_time", 0.0)),
        end_time=None if end_time is None else float(end_time),
        fade_in=float(d.get("fade_in", 0.0)),
        end_fade=float(d.get("end_fade", 0.0)),
        stop_fade=float(d.get("stop_fade", DEFAULT_STOP_FADE)),
        repeats=int(d.get("repeats", 0)),
        continue_mode=_continue_mode(d.get("continue_mode")),
        continue_delay=max(0.0, float(d.get("continue_delay", 0.0))),
        loop_section=bool(d.get("loop_section", False)),
        loop_start=max(0.0, float(d.get("loop_start", 0.0))),
        loop_end=None if loop_end is None else float(loop_end),
        loop_first=d.get("loop_first") if d.get("loop_first") in LOOP_FIRST_MODES else FIRST_FULL,
        loop_after=d.get("loop_after") if d.get("loop_after") in LOOP_AFTER_MODES else AFTER_STOP,
        loop_fade_in=max(0.0, float(d.get("loop_fade_in", 0.0))),
        loop_fade_out=max(0.0, float(d.get("loop_fade_out", 0.0))),
        loop_fade_mode=(
            d.get("loop_fade_mode")
            if d.get("loop_fade_mode") in LOOP_FADE_MODES
            else LOOP_FADE_JUMP
        ),
        id=str(d.get("id") or new_cue_id()),
    )


@dataclass
class ShowData:
    cues: list[AudioCue] = field(default_factory=list)
    master_db: float = 0.0


def save_show(
    path: str | Path,
    cues: list[AudioCue] | tuple[AudioCue, ...],
    master_db: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> None:
    path = Path(path)
    show_dir = path.parent.resolve()
    doc = {
        "app": "WinQ",
        "format_version": FORMAT_VERSION,
        "master_db": master_db,
        "cues": [cue_to_dict(c, show_dir) for c in cues],
        **(extra or {}),
    }
    # Write to a temp file then replace, so a crash mid-save never corrupts the show.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_show(path: str | Path) -> ShowData:
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ShowFileError(f"Could not open {path.name}: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ShowFileError(f"{path.name} is not a valid WinQ show file") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("cues"), list):
        raise ShowFileError(f"{path.name} is not a valid WinQ show file")
    version = doc.get("format_version", 1)
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise ShowFileError(f"{path.name} was saved by a newer version of WinQ (format {version})")
    show_dir = path.parent.resolve()
    try:
        cues = [cue_from_dict(d, show_dir) for d in doc["cues"]]
        master_db = float(doc.get("master_db", 0.0))
        return ShowData(cues, master_db)
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise ShowFileError(f"{path.name} contains an invalid cue: {exc}") from exc


def read_autosave_meta(path: str | Path) -> tuple[Path | None, str]:
    """For an autosave backup: (the show it belongs to or None if untitled, when it
    was saved as an ISO timestamp). Raises ShowFileError if unreadable."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ShowFileError(f"Unreadable autosave {Path(path).name}") from exc
    original = doc.get("autosave_of") if isinstance(doc, dict) else None
    saved_at = str(doc.get("autosave_at", "")) if isinstance(doc, dict) else ""
    return (Path(original) if original else None), saved_at
