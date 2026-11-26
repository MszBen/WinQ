"""Sample-exact tests for Voice: trim, repeats, devamp, fades, stop, gain smoothing.

A low sample rate keeps numbers readable: at 1000 Hz the gain ramp is 10 frames and the
minimum stop ramp is 5 frames.
"""

from __future__ import annotations

import numpy as np
import pytest

from winq.audio.voice import INFINITE, Voice

SR = 1000


def ramp_buffer(n: int) -> np.ndarray:
    """Frame i holds the value i on both channels, so output reveals buffer positions."""
    col = np.arange(n, dtype=np.float32)
    return np.stack([col, col], axis=1)


def ones_buffer(n: int) -> np.ndarray:
    return np.ones((n, 2), dtype=np.float32)


def render_all(voice: Voice, total: int, block: int) -> np.ndarray:
    chunks = []
    done = 0
    while done < total:
        n = min(block, total - done)
        chunks.append(voice.render(n))
        done += n
    return np.concatenate(chunks)[:, 0]


@pytest.mark.parametrize("block", [1, 7, 64, 1000])
def test_trimmed_single_pass_is_sample_exact(block):
    v = Voice(1, ramp_buffer(100), SR, start_frame=10, end_frame=40)
    out = render_all(v, 50, block)
    np.testing.assert_array_equal(out[:30], np.arange(10, 40))
    np.testing.assert_array_equal(out[30:], 0)
    assert v.finished


@pytest.mark.parametrize("block", [1, 7, 64])
def test_fixed_repeats_play_all_passes(block):
    v = Voice(1, ramp_buffer(100), SR, start_frame=5, end_frame=15, repeats=2)
    out = render_all(v, 40, block)
    one_pass = np.arange(5, 15)
    np.testing.assert_array_equal(out[:30], np.tile(one_pass, 3))
    np.testing.assert_array_equal(out[30:], 0)
    assert v.finished


def test_fixed_repeats_ignore_devamp():
    v = Voice(1, ramp_buffer(20), SR, repeats=1)
    v.render(5)
    v.devamp()
    out = render_all(v, 40, 8)
    assert np.count_nonzero(out) > 20  # second pass still played (frame 0 is a zero value)
    assert v.frames_played == 40


def test_infinite_loops_until_devamp_then_finishes_pass():
    v = Voice(1, ramp_buffer(10), SR, repeats=INFINITE)
    out = render_all(v, 1000, 33)
    assert not v.finished and v.is_looping
    np.testing.assert_array_equal(out, np.tile(np.arange(10), 100))

    v.render(3)  # now 3 frames into a pass
    v.devamp()
    assert not v.is_looping and v.is_final_pass
    tail = render_all(v, 20, 4)
    np.testing.assert_array_equal(tail[:7], np.arange(3, 10))
    np.testing.assert_array_equal(tail[7:], 0)
    assert v.finished


def test_fade_in_first_pass_only():
    v = Voice(1, ones_buffer(20), SR, repeats=1, fade_in_frames=10)
    out = render_all(v, 40, 6)
    assert out[0] == 0.0
    assert np.all(np.diff(out[:10]) > 0)  # rising
    np.testing.assert_allclose(out[10:20], 1.0)
    np.testing.assert_allclose(out[20:40], 1.0)  # second pass: no fade-in


def test_end_fade_final_pass_only():
    v = Voice(1, ones_buffer(20), SR, repeats=1, end_fade_frames=10)
    out = render_all(v, 40, 6)
    np.testing.assert_allclose(out[:20], 1.0)  # first pass: no end fade
    np.testing.assert_allclose(out[20:30], 1.0)
    assert np.all(np.diff(out[30:40]) < 0)  # falling
    assert out[39] == pytest.approx(0.0, abs=1e-6)


def test_devamp_before_end_fade_zone_uses_full_fade():
    v = Voice(1, ones_buffer(20), SR, repeats=INFINITE, end_fade_frames=10)
    v.render(25)  # 5 frames into the 2nd pass
    v.devamp()
    out = render_all(v, 15, 15)
    np.testing.assert_allclose(out[:5], 1.0)
    assert np.all(np.diff(out[5:15]) < 0)
    assert out[14] == pytest.approx(0.0, abs=1e-6)
    assert v.finished


def test_devamp_inside_end_fade_zone_shortens_fade_without_jump():
    v = Voice(1, ones_buffer(20), SR, repeats=INFINITE, end_fade_frames=10)
    v.render(16)  # 4 frames left in this pass, already inside the would-be fade zone
    v.devamp()
    out = render_all(v, 4, 4)
    assert out[0] > 0.6  # starts near full level: no sudden drop
    assert np.all(np.diff(out) < 0)
    assert out[3] == pytest.approx(0.0, abs=1e-6)
    assert v.finished


def test_stop_fades_from_current_point_and_finishes():
    v = Voice(1, ones_buffer(1000), SR, repeats=INFINITE)
    v.render(100)
    v.stop(50)
    out = render_all(v, 60, 7)
    assert out[0] < 1.0
    assert np.all(np.diff(out[:50]) <= 0)
    assert out[49] == pytest.approx(0.0, abs=1e-6)
    np.testing.assert_array_equal(out[50:], 0)
    assert v.finished


def test_instant_stop_still_gets_declick_ramp():
    v = Voice(1, ones_buffer(1000), SR)
    v.stop(0)
    out = v.render(10)[:, 0]
    assert 0 < out[0] < 1  # not a hard cut
    assert out[4] == pytest.approx(0.0, abs=1e-6)  # 5 ms at 1 kHz
    assert v.finished


def test_second_stop_is_ignored():
    v = Voice(1, ones_buffer(1000), SR)
    v.stop(100)
    v.render(10)
    v.stop(5)
    v.render(20)
    assert not v.finished  # still on the original 100-frame fade


def test_gain_change_is_smoothed():
    v = Voice(1, ones_buffer(1000), SR, gain=1.0)
    v.render(5)
    v.set_gain(0.5)
    out = v.render(20)[:, 0]
    assert np.all(np.diff(out[:10]) < 0)  # glides over 10 frames
    assert out[0] > 0.9
    np.testing.assert_allclose(out[9:], 0.5)
    assert v.gain == 0.5


def test_invalid_trim_rejected():
    with pytest.raises(ValueError):
        Voice(1, ones_buffer(10), SR, start_frame=5, end_frame=5)
    with pytest.raises(ValueError):
        Voice(1, ones_buffer(10), SR, end_frame=11)


# ------------------------------------------------------------------ repeat section
# Buffer 0..99, trim Start=10 End=90, section Loop start=40 Loop end=60.
S, E, LS, LE = 10, 90, 40, 60


def section_voice(repeats: int, *, first_full: bool = True, outro: bool = False, **kw) -> Voice:
    return Voice(1, ramp_buffer(100), SR, start_frame=S, end_frame=E, repeats=repeats,
                 loop_start_frame=LS, loop_end_frame=LE, first_full=first_full, outro=outro,
                 **kw)  # fmt: skip


def play_to_end(voice: Voice, block: int = 7, limit: int = 10_000) -> np.ndarray:
    """Everything the voice plays, until it finishes on its own."""
    chunks = []
    while not voice.finished and limit > 0:
        out = voice.render(block)
        chunks.append(out[: voice.last_produced, 0])
        limit -= block
    assert voice.finished
    return np.concatenate(chunks)


def frames(*ranges: tuple[int, int]) -> np.ndarray:
    return np.concatenate([np.arange(a, b, dtype=np.float32) for a, b in ranges])


def test_section_whole_track_first_then_section_repeats():
    v = section_voice(2)
    assert np.array_equal(play_to_end(v), frames((S, E), (LS, LE), (LS, LE)))
    assert v.ended_naturally


def test_section_intro_into_the_section():
    v = section_voice(2, first_full=False)
    assert np.array_equal(play_to_end(v), frames((S, LE), (LS, LE), (LS, LE)))


def test_section_plays_on_to_the_end_after_the_last_repeat():
    v = section_voice(1, outro=True)
    assert np.array_equal(play_to_end(v), frames((S, E), (LS, LE), (LE, E)))
    v = section_voice(1, first_full=False, outro=True)
    assert np.array_equal(play_to_end(v), frames((S, LE), (LS, LE), (LE, E)))


def test_section_without_repeats():
    # Whole-track first play: nothing repeats, so no outro is added.
    assert np.array_equal(play_to_end(section_voice(0, outro=True)), frames((S, E)))
    # Intro first play then outro: the whole track, seamlessly.
    v = section_voice(0, first_full=False, outro=True)
    assert np.array_equal(play_to_end(v), frames((S, E)))


@pytest.mark.parametrize("outro", [False, True])
def test_infinite_section_loops_until_devamp_then_ends_or_plays_on(outro):
    v = section_voice(INFINITE, outro=outro)
    played = [v.render(80 + 20 * 3 + 5)[:, 0]]  # first play + 3 loops + 5 frames in
    assert v.remaining_frames() is None and v.is_looping
    v.devamp()  # Space: finish this pass
    assert not v.is_looping
    assert v.remaining_frames() == 15 + (E - LE if outro else 0)
    played.append(play_to_end(v))
    tail = (LE, E) if outro else (LE, LE)
    expected = frames((S, E), (LS, LE), (LS, LE), (LS, LE), (LS, LE), tail)
    assert np.array_equal(np.concatenate(played), expected)


def test_infinite_section_devamped_during_the_first_play_ends_at_end():
    v = section_voice(INFINITE)
    first = v.render(30)[:, 0]
    v.devamp()
    assert np.array_equal(np.concatenate([first, play_to_end(v)]), frames((S, E)))


def test_section_end_fade_lands_on_loop_end_of_the_last_pass_only():
    v = Voice(1, ones_buffer(100), SR, start_frame=S, end_frame=E, repeats=1,
              loop_start_frame=LS, loop_end_frame=LE, end_fade_frames=10)  # fmt: skip
    out = play_to_end(v)
    first, last = out[: E - S], out[E - S :]
    assert np.all(first == 1.0)  # the first (whole) play is not the last: no fade
    assert last[0] == 1.0 and last[-1] == pytest.approx(0.0, abs=1e-6)
    assert last[-11] == 1.0 and last[-9] < 1.0  # the fade covers the last 10 frames


def test_section_remaining_frames_counts_every_segment():
    for kw in ({}, {"first_full": False}, {"outro": True}, {"first_full": False, "outro": True}):
        v = section_voice(2, **kw)
        expected = len(play_to_end(section_voice(2, **kw)))
        assert v.remaining_frames() == expected
        v.render(13)
        assert v.remaining_frames() == expected - 13


def test_section_must_lie_inside_the_trim():
    with pytest.raises(ValueError):
        Voice(1, ramp_buffer(100), SR, start_frame=S, end_frame=E,
              loop_start_frame=5, loop_end_frame=60)  # fmt: skip
    with pytest.raises(ValueError):  # intro: a preview can't begin after Loop end
        section_voice(1, first_full=False, begin_frame=70)
