"""Every GO / Escape / panic rule from CLAUDE.md §1, against a fake player."""

from __future__ import annotations

from pathlib import Path

import pytest

from winq.control.transport import GoAction, Transport
from winq.model.cue import INFINITE_REPEATS, AudioCue


class FakePlayer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._next = 1
        self.unplayable: set[str] = set()

    def start(self, cue: AudioCue) -> int | None:
        if cue.id in self.unplayable:
            self.calls.append(("start-failed", cue.name))
            return None
        vid = self._next
        self._next += 1
        self.calls.append(("start", cue.name, vid))
        return vid

    def devamp(self, voice_id: int) -> None:
        self.calls.append(("devamp", voice_id))

    def stop(self, voice_id: int, fade_seconds: float) -> None:
        self.calls.append(("stop", voice_id, fade_seconds))

    def stop_all(self, fade_seconds: float) -> None:
        self.calls.append(("stop_all", fade_seconds))


def cue(name: str, **kw) -> AudioCue:
    c = AudioCue(file_path=Path(f"C:/a/{name}.wav"), name=name, **kw)
    c.file_duration = 10.0
    return c


@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def transport(player):
    return Transport(player)


def test_go_starts_selected_and_advances(transport, player):
    cues = [cue("a"), cue("b")]
    r = transport.go(cues, 0)
    assert r.action is GoAction.STARTED and r.selection == 1 and r.voice_id == 1
    assert player.calls == [("start", "a", 1)]
    r = transport.go(cues, 1)
    assert r.selection == 1  # last cue: selection stays
    assert [x.cue.name for x in transport.running] == ["a", "b"]  # both playing at once


def test_go_with_no_selection_or_empty_list(transport, player):
    assert transport.go([], None).action is GoAction.NOTHING
    assert transport.go([cue("a")], None).action is GoAction.NOTHING
    assert transport.go([cue("a")], 5).action is GoAction.NOTHING
    assert player.calls == []


def test_infinite_cue_keeps_selection_then_go_ends_loop(transport, player):
    cues = [cue("loop", repeats=INFINITE_REPEATS), cue("next")]
    r = transport.go(cues, 0)
    assert r.action is GoAction.STARTED and r.selection == 0  # stays on the looping cue
    assert transport.running[0].looping

    r = transport.go(cues, 0)
    assert r.action is GoAction.LOOP_ENDED and r.selection == 1
    assert player.calls[-1] == ("devamp", 1)
    assert not transport.running[0].looping  # still playing its final pass
    assert len(transport.running) == 1  # nothing new started

    r = transport.go(cues, 1)
    assert r.action is GoAction.STARTED and r.cue.name == "next"


def test_go_on_loop_ended_cue_starts_a_new_looping_copy(transport, player):
    cues = [cue("loop", repeats=INFINITE_REPEATS)]
    transport.go(cues, 0)
    transport.go(cues, 0)  # ends the loop; the first copy finishes its pass
    r = transport.go(cues, 0)  # user went back to it
    assert r.action is GoAction.STARTED and r.voice_id == 2
    r = transport.go(cues, 0)
    assert r.action is GoAction.LOOP_ENDED and r.voice_id == 2  # ends the one still looping


def test_go_ends_the_loop_of_the_selected_cue_only(transport, player):
    cues = [cue("rain", repeats=INFINITE_REPEATS), cue("wind", repeats=INFINITE_REPEATS)]
    transport.go(cues, 0)  # rain loops; selection stays on rain
    transport.go(cues, 1)  # user moved to wind and started it
    r = transport.go(cues, 0)  # back on rain
    assert r.action is GoAction.LOOP_ENDED and r.voice_id == 1
    assert transport.looping_instance(cues[1].id) is not None  # wind still loops


def test_fixed_repeats_are_never_cut_short_by_go(transport, player):
    cues = [cue("twice", repeats=2), cue("b")]
    transport.go(cues, 0)
    r = transport.go(cues, 0)  # reselected while playing: overlapping copy, no devamp
    assert r.action is GoAction.STARTED and r.selection == 1
    assert not any(c[0] == "devamp" for c in player.calls)


def test_go_on_playing_cue_starts_overlapping_copy(transport, player):
    cues = [cue("a"), cue("b")]
    transport.go(cues, 0)
    r = transport.go(cues, 0)
    assert r.action is GoAction.STARTED and r.voice_id == 2
    assert len(transport.instances_of(cues[0].id)) == 2


def test_missing_cue_is_skipped_and_selection_advances(transport, player):
    cues = [cue("gone"), cue("b")]
    player.unplayable.add(cues[0].id)
    r = transport.go(cues, 0)
    assert r.action is GoAction.SKIPPED and r.selection == 1 and r.cue is cues[0]
    assert transport.running == ()


def test_escape_stops_in_start_order_with_each_cues_stop_fade(transport, player):
    cues = [cue("one", stop_fade=2.0), cue("two", stop_fade=0.25), cue("three")]
    for i in range(3):
        transport.go(cues, i)
    assert transport.stop_oldest().cue.name == "one"
    assert player.calls[-1] == ("stop", 1, 2.0)
    assert transport.stop_oldest().cue.name == "two"  # "one" is already stopping: skipped
    assert player.calls[-1] == ("stop", 2, 0.25)
    cues[2].stop_fade = 4.0  # edited while playing: the current value is used
    transport.stop_oldest()
    assert player.calls[-1] == ("stop", 3, 4.0)
    assert transport.stop_oldest() is None
    assert len(transport.running) == 3  # still listed until the engine says they ended


def test_escape_on_a_looping_cue_then_go_starts_fresh(transport, player):
    cues = [cue("loop", repeats=INFINITE_REPEATS)]
    transport.go(cues, 0)
    transport.stop_oldest()
    assert transport.looping_instance(cues[0].id) is None
    assert transport.go(cues, 0).action is GoAction.STARTED


def test_ended_voices_leave_the_queue(transport, player):
    cues = [cue("a"), cue("b")]
    transport.go(cues, 0)
    transport.go(cues, 1)
    assert transport.voice_ended(1).cue.name == "a"
    assert transport.voice_ended(99) is None
    assert transport.stop_oldest().cue.name == "b"


def test_panic_stops_everything(transport, player):
    cues = [cue("a"), cue("loop", repeats=INFINITE_REPEATS)]
    transport.go(cues, 0)
    transport.go(cues, 1)
    transport.panic(0.5)
    assert player.calls[-1] == ("stop_all", 0.5)
    assert all(r.stopping and not r.looping for r in transport.running)
    assert transport.stop_oldest() is None


def test_stop_individual_voice(transport, player):
    cues = [cue("a"), cue("b")]
    transport.go(cues, 0)
    transport.go(cues, 1)
    transport.stop_voice(2)
    assert player.calls[-1] == ("stop", 2, 1.0)
    transport.stop_voice(2)  # second press ignored
    assert player.calls[-1] == ("stop", 2, 1.0)
    assert transport.stop_oldest().cue.name == "a"
