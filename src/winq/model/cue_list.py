"""Ordered list of cues. Pure Python; the Qt table model wraps it."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator

from winq.model.cue import AudioCue


class CueList:
    def __init__(self, cues: Iterable[AudioCue] = ()) -> None:
        self._cues: list[AudioCue] = list(cues)

    def __len__(self) -> int:
        return len(self._cues)

    def __getitem__(self, index: int) -> AudioCue:
        return self._cues[index]

    def __iter__(self) -> Iterator[AudioCue]:
        return iter(self._cues)

    @property
    def cues(self) -> tuple[AudioCue, ...]:
        return tuple(self._cues)

    def index_of(self, cue_id: str) -> int | None:
        for i, cue in enumerate(self._cues):
            if cue.id == cue_id:
                return i
        return None

    def insert(self, index: int, cues: Iterable[AudioCue]) -> None:
        index = max(0, min(index, len(self._cues)))
        self._cues[index:index] = list(cues)

    def remove(self, index: int, count: int = 1) -> list[AudioCue]:
        removed = self._cues[index : index + count]
        del self._cues[index : index + count]
        return removed

    def move(self, src: int, final_index: int) -> None:
        """Move the cue at `src` so it ends up at `final_index`."""
        cue = self._cues.pop(src)
        self._cues.insert(final_index, cue)

    def replace_all(self, cues: Iterable[AudioCue]) -> None:
        self._cues = list(cues)

    def next_number(self) -> int:
        """One more than the highest whole-number part of any numeric cue number (1 if none)."""
        highest = 0
        for cue in self._cues:
            try:
                value = float(cue.number)
            except ValueError:
                continue
            if math.isfinite(value):
                highest = max(highest, math.floor(value))
        return highest + 1
