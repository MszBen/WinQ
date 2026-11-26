"""GO / Escape / panic / auto-follow rules (CLAUDE.md §1). Plain Python: no Qt, no audio.

The Transport decides *what* happens; a `Player` makes sound. It keeps every instance
it started:
- **started** instances in the order they actually began. That order is the Escape
  order and the Active Cues order.
- **pending** instances: scheduled by auto-follow/continue, not audible yet. Escape
  skips them; Stop All cancels them.

When GO starts a cue whose "Then" setting is follow or continue, the whole chain of
following cues is scheduled in the engine at once, so the handoffs are sample-accurate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

from winq.model.cue import CONTINUE, FOLLOW, NO_CONTINUE, AudioCue


class Player(Protocol):
    def start(
        self,
        cue: AudioCue,
        *,
        delay: float = 0.0,
        after: int | None = None,
        parent: int | None = None,
    ) -> int | None:
        """Start (or schedule) a new instance of `cue`. Returns its voice id, or None if
        it can't play. `after`: start `delay` s after that voice ends naturally.
        `parent`: cancelled if that voice is stopped before this one starts."""
        ...

    def start_now(self, voice_id: int) -> None: ...

    def cancel(self, voice_id: int) -> None: ...

    def devamp(self, voice_id: int) -> None: ...

    def stop(self, voice_id: int, fade_seconds: float) -> None: ...

    def stop_all(self, fade_seconds: float) -> None: ...


class GoAction(Enum):
    NOTHING = auto()  # no cues / no selection
    STARTED = auto()
    LOOP_ENDED = auto()  # devamped a looping instance of the selected cue
    SKIPPED = auto()  # cue could not play (missing/unreadable file)


@dataclass
class RunningInstance:
    voice_id: int
    cue: AudioCue
    looping: bool  # an infinite cue that hasn't had its loop ended yet
    stopping: bool = False
    pending: bool = False  # scheduled by auto-follow/continue, not started yet
    loop_ended: bool = False  # GO has ended this instance's loop


@dataclass(frozen=True)
class GoResult:
    action: GoAction
    selection: int | None  # where the selection should be afterwards
    cue: AudioCue | None = None
    voice_id: int | None = None
    chain_broken_at: AudioCue | None = None  # a follow-on cue that couldn't be scheduled


def selection_after(cues: Sequence[AudioCue], index: int) -> int:
    """Where the selection goes after `cues[index]` has been GO'd: past every cue that
    will start automatically from it. A loop-forever cue inside the chain stops the
    skip, so the next Space ends its loop."""
    j = index
    while cues[j].continue_mode != NO_CONTINUE and j + 1 < len(cues):
        j += 1
        if cues[j].is_infinite:
            return j
    return min(j + 1, len(cues) - 1)


class Transport:
    def __init__(self, player: Player) -> None:
        self._player = player
        self._started: list[RunningInstance] = []
        self._pending: list[RunningInstance] = []

    # ----------------------------------------------------------------- queries
    @property
    def running(self) -> tuple[RunningInstance, ...]:
        """Started instances (oldest first = Escape order), then pending ones."""
        return (*self._started, *self._pending)

    @property
    def started(self) -> tuple[RunningInstance, ...]:
        return tuple(self._started)

    def instances_of(self, cue_id: str) -> list[RunningInstance]:
        return [r for r in self.running if r.cue.id == cue_id]

    def looping_instance(self, cue_id: str) -> RunningInstance | None:
        """Oldest started instance of this cue that is still looping (the one GO ends)."""
        for r in self._started:
            if r.cue.id == cue_id and r.looping and not r.stopping:
                return r
        return None

    def pending_instance(self, cue_id: str) -> RunningInstance | None:
        for r in self._pending:
            if r.cue.id == cue_id:
                return r
        return None

    # ----------------------------------------------------------------- GO
    def go(self, cues: Sequence[AudioCue], selected: int | None) -> GoResult:
        if selected is None or not 0 <= selected < len(cues):
            return GoResult(GoAction.NOTHING, selected)
        cue = cues[selected]
        after_chain = selection_after(cues, selected)

        if cue.is_infinite and (loop := self.looping_instance(cue.id)) is not None:
            self._player.devamp(loop.voice_id)
            loop.looping = False
            loop.loop_ended = True
            return GoResult(GoAction.LOOP_ENDED, after_chain, cue, loop.voice_id)

        stay = selected if cue.is_infinite else after_chain
        if (waiting := self.pending_instance(cue.id)) is not None:
            # It was going to start by itself; GO starts it now (its chain stays attached).
            self._player.start_now(waiting.voice_id)
            return GoResult(GoAction.STARTED, stay, cue, waiting.voice_id)

        voice_id = self._player.start(cue)
        if voice_id is None:
            return GoResult(GoAction.SKIPPED, min(selected + 1, len(cues) - 1), cue)
        self._started.append(RunningInstance(voice_id, cue, looping=cue.is_infinite))
        broken = self._schedule_chain(cues, selected, voice_id)
        return GoResult(GoAction.STARTED, stay, cue, voice_id, chain_broken_at=broken)

    def _schedule_chain(
        self, cues: Sequence[AudioCue], index: int, voice_id: int
    ) -> AudioCue | None:
        """Schedule every cue that follows/continues from cues[index]. Returns the cue
        where the chain broke (file missing), if any."""
        prev_id, j = voice_id, index
        while cues[j].continue_mode != NO_CONTINUE and j + 1 < len(cues):
            mode, delay, nxt = cues[j].continue_mode, cues[j].continue_delay, cues[j + 1]
            if mode == FOLLOW:
                vid = self._player.start(nxt, delay=delay, after=prev_id)
            elif mode == CONTINUE:
                vid = self._player.start(nxt, delay=delay, parent=prev_id)
            else:  # pragma: no cover - unknown mode from a newer show file
                return None
            if vid is None:
                return nxt
            self._pending.append(RunningInstance(vid, nxt, looping=nxt.is_infinite, pending=True))
            prev_id, j = vid, j + 1
        return None

    def repeats_changed(self, cue_id: str) -> None:
        """A cue's repeats were edited while copies play (live edit): copies that now
        loop forever wait for GO to end the loop; copies with a fixed count don't."""
        for r in self.running:
            if r.cue.id == cue_id and not r.stopping:
                r.looping = r.cue.is_infinite and not r.loop_ended

    # ----------------------------------------------------------------- stopping
    def stop_oldest(self) -> RunningInstance | None:
        """Escape: fade out the earliest-started instance that isn't already stopping.
        Anything scheduled to follow it is cancelled by the engine."""
        for r in self._started:
            if not r.stopping:
                self._stop(r)
                return r
        return None

    def stop_voice(self, voice_id: int) -> None:
        for r in self._started:
            if r.voice_id == voice_id and not r.stopping:
                self._stop(r)
                return
        for r in self._pending:
            if r.voice_id == voice_id and not r.stopping:
                self._player.cancel(voice_id)
                r.stopping = True
                return

    def panic(self, fade_seconds: float) -> None:
        """Stop everything playing and cancel every pending automatic start."""
        self._player.stop_all(fade_seconds)
        for r in self.running:
            r.stopping = True
            r.looping = False

    def _stop(self, r: RunningInstance) -> None:
        self._player.stop(r.voice_id, r.cue.stop_fade)
        r.stopping = True
        r.looping = False

    # ----------------------------------------------------------------- engine feedback
    def voice_started(self, voice_id: int) -> RunningInstance | None:
        """A pending instance became audible: it joins the Escape queue at the end."""
        for i, r in enumerate(self._pending):
            if r.voice_id == voice_id:
                r = self._pending.pop(i)
                r.pending = False
                self._started.append(r)
                return r
        return None

    def voice_ended(self, voice_id: int) -> RunningInstance | None:
        """A started instance finished, or a pending one was cancelled."""
        for group in (self._started, self._pending):
            for i, r in enumerate(group):
                if r.voice_id == voice_id:
                    return group.pop(i)
        return None

    voice_cancelled = voice_ended
