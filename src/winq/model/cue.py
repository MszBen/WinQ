"""The AudioCue data model. Times are in seconds, levels in dB (see CLAUDE.md §7)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

INFINITE_REPEATS = -1  # must equal winq.audio.voice.INFINITE (checked by a test)

DEFAULT_LEVEL_DB = 0.0
DEFAULT_STOP_FADE = 1.0  # seconds; used when Escape stops the cue
DEFAULT_PANIC_FADE = 0.5  # seconds; Shift+Esc default (a preference)
MIN_LEVEL_DB = -60.0  # at or below this the UI shows -inf
MAX_LEVEL_DB = 12.0
MAX_MASTER_DB = 6.0

# "Then" setting: what this cue does to the NEXT cue in the list.
NO_CONTINUE = "none"  # nothing; the next cue waits for Space
FOLLOW = "follow"  # next cue starts when this one ends naturally (+ delay)
CONTINUE = "continue"  # next cue starts `continue_delay` s after this one starts
CONTINUE_MODES = (NO_CONTINUE, FOLLOW, CONTINUE)

# Repeat section ("loop_section"): repeats play only Loop start→Loop end.
FIRST_FULL = "full"  # the first play runs Start→End in full, then the section repeats
FIRST_INTRO = "intro"  # the first play runs Start→Loop end, straight into the section
LOOP_FIRST_MODES = (FIRST_FULL, FIRST_INTRO)
AFTER_STOP = "stop"  # after the last repeat the cue ends at Loop end
AFTER_OUTRO = "outro"  # after the last repeat it plays on from Loop end to End
LOOP_AFTER_MODES = (AFTER_STOP, AFTER_OUTRO)

# Loop fades: how a repeating cue sounds where playback jumps back to the loop start.
LOOP_FADE_JUMP = "jump"  # dip (fade out, fade in) at every jump back
LOOP_FADE_LOOP_END = "loop_end"  # dip, but the jump back from End after a whole first
# play only fades in (the fade-out sits before Loop end only)
LOOP_FADE_PASS = "pass"  # every pass fades in and out, also before the outro or the stop
LOOP_FADE_CROSSFADE = "crossfade"  # no dip: blend the pass's tail with the audio leading
# into the loop start (length = loop_fade_out)
LOOP_FADE_MODES = (LOOP_FADE_JUMP, LOOP_FADE_LOOP_END, LOOP_FADE_PASS, LOOP_FADE_CROSSFADE)


def new_cue_id() -> str:
    return uuid.uuid4().hex


@dataclass
class AudioCue:
    file_path: Path  # always absolute in memory; made relative only when saving
    number: str = ""
    name: str = ""
    notes: str = ""
    level_db: float = DEFAULT_LEVEL_DB
    start_time: float = 0.0
    end_time: float | None = None  # None = end of file
    fade_in: float = 0.0
    end_fade: float = 0.0
    stop_fade: float = DEFAULT_STOP_FADE
    repeats: int = 0  # extra passes; INFINITE_REPEATS loops until GO ends the loop
    continue_mode: str = NO_CONTINUE  # one of CONTINUE_MODES
    continue_delay: float = 0.0  # seconds (after this cue's end for FOLLOW, start for CONTINUE)
    loop_section: bool = False  # repeats play only loop_start→loop_end (file seconds)
    loop_start: float = 0.0
    loop_end: float | None = None  # None = the cue's End
    loop_first: str = FIRST_FULL  # one of LOOP_FIRST_MODES
    loop_after: str = AFTER_STOP  # one of LOOP_AFTER_MODES
    loop_fade_in: float = 0.0  # seconds; fade up from the loop start after a jump back
    loop_fade_out: float = 0.0  # seconds; fade down before the jump (crossfade length)
    loop_fade_mode: str = LOOP_FADE_JUMP  # one of LOOP_FADE_MODES
    id: str = field(default_factory=new_cue_id)

    # Runtime only (not saved): filled in by probing the file.
    file_duration: float | None = field(default=None, compare=False, repr=False)

    @property
    def is_infinite(self) -> bool:
        return self.repeats == INFINITE_REPEATS

    @property
    def is_missing(self) -> bool:
        """True when the file could not be found or read at the last probe."""
        return self.file_duration is None

    @property
    def effective_end(self) -> float | None:
        if self.file_duration is None:
            return self.end_time
        if self.end_time is None:
            return self.file_duration
        return min(self.end_time, self.file_duration)

    def section_bounds(self) -> tuple[float, float] | None:
        """(loop start, loop end) in file seconds, kept inside Start→End, or None when
        no section is set (or the cue's end is unknown)."""
        end = self.effective_end
        if not self.loop_section or end is None:
            return None
        lo = min(max(self.loop_start, self.start_time), end)
        hi = end if self.loop_end is None else min(max(self.loop_end, lo), end)
        return (lo, hi) if hi > lo else None

    @property
    def has_loop_fades(self) -> bool:
        """True when loop fades are set (they only play on a cue that repeats)."""
        if self.loop_fade_mode == LOOP_FADE_CROSSFADE:
            return self.loop_fade_out > 0
        return self.loop_fade_in > 0 or self.loop_fade_out > 0

    @property
    def pass_duration(self) -> float | None:
        """Length of one pass through the trimmed region, or None if unknown."""
        end = self.effective_end
        if end is None:
            return None
        return max(0.0, end - self.start_time)
