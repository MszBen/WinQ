"""One playing instance of a cue.

A Voice is created on the GUI thread, then owned by the audio thread: after it is handed
to the engine, only the engine calls its mutating methods (via commands). The GUI may
*read* `position`, `pass_index`, `finished` etc. for display.

All times are in frames at the engine sample rate.

Playback model
--------------
A voice plays a sequence of *segments*:

1. **first**: start_frame → first_end_frame (the cue's End, or Loop end for "intro").
2. **loop** passes: loop_start_frame → loop_end_frame. `repeats` of them, or until
   `devamp()` for `INFINITE`. Without a repeat section the loop region is the whole
   Start→End trim, which is the original behaviour.
3. **outro** (optional): loop_end_frame → end_frame, only after a segment that ended at
   Loop end.

- Fade-in applies on the first segment only; the end fade on the last segment only.
- `devamp()` (infinite voices only) makes the current pass the last loop pass. If the
  current segment is then the last one and we are inside its end-fade zone, the fade is
  shortened to start right now, so it still ends on time without a jump in level.
- `stop(frames)` fades the voice out from wherever it is (Escape).

Loop fades (only on a voice that repeats), by `loop_fade_mode`:

- JUMP: every segment that is followed by a jump back to Loop start fades out over
  `loop_fade_out_frames` before its end, and every loop pass fades in over
  `loop_fade_in_frames` from Loop start (a "dip" at each loop point).
- LOOP_END: the same, but only segments ending at Loop end fade out (the jump back
  from End after a whole first play only fades in).
- PASS: like JUMP, and also every segment ending at Loop end fades out even when nothing
  jumps (before the outro, or the stop); an outro after a dip fades in.
- CROSSFADE: no dip. Over the last `loop_fade_out_frames` before a jump, the tail fades
  out while the audio just before Loop start fades in, so the jump is seamless. Pass
  lengths don't change. Needs file audio before Loop start (shorter if there isn't).

Once a loop fade-out (or crossfade) has begun it always completes: devamping inside a
dip lets the dip finish (a following outro fades back in); devamping inside a crossfade
lets the jump happen, and the pass after it is the last one.

Live edits
----------
`reshape(shape)` replaces every trim / fade / repeat / loop setting while the voice
plays (the GUI builds the `Shape` from the cue). The voice stays in its current phase and
pass; its segment is recomputed from the new values. If the playhead is now past the
segment's end it acts as if it had just reached it (jumps back, plays on, or ends
naturally); if it is before the segment's start it jumps forward to it. Every such jump
gets a 5 ms crossfade (de-click). Fades are recomputed from the current position, so a
fade already under way may change level when its length is edited.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from winq.audio.dsp import (
    GAIN_RAMP_SECONDS,
    MIN_STOP_SECONDS,
    GainRamp,
    fade_in_curve,
    fade_out_curve,
    seconds_to_frames,
)
from winq.model.cue import LOOP_FADE_CROSSFADE as CROSSFADE
from winq.model.cue import LOOP_FADE_JUMP as JUMP
from winq.model.cue import LOOP_FADE_LOOP_END as LOOP_END
from winq.model.cue import LOOP_FADE_MODES

INFINITE = -1
FIRST, LOOP, OUTRO = "first", "loop", "outro"


@dataclass(frozen=True, slots=True)
class Shape:
    """Everything about how a voice plays its buffer that can change while it plays."""

    start_frame: int
    end_frame: int
    loop_start_frame: int
    loop_end_frame: int
    first_full: bool = True
    outro: bool = False
    repeats: int = 0
    fade_in_frames: int = 0
    end_fade_frames: int = 0
    loop_fade_in_frames: int = 0
    loop_fade_out_frames: int = 0
    loop_fade_mode: str = JUMP

    def problem(self, total: int) -> str | None:
        """Why this shape can't play a buffer of `total` frames, or None if it can."""
        if not 0 <= self.start_frame < self.end_frame <= total:
            return f"invalid trim: start={self.start_frame} end={self.end_frame} total={total}"
        if not self.start_frame <= self.loop_start_frame < self.loop_end_frame <= self.end_frame:
            return (
                f"invalid loop section: {self.loop_start_frame}..{self.loop_end_frame} "
                f"outside [{self.start_frame}, {self.end_frame}]"
            )
        if self.repeats < 0 and self.repeats != INFINITE:
            return "repeats must be >= 0 or INFINITE"
        if self.loop_fade_mode not in LOOP_FADE_MODES:
            return f"unknown loop fade mode {self.loop_fade_mode!r}"
        return None


class Voice:
    def __init__(
        self,
        voice_id: int,
        buffer: np.ndarray,
        samplerate: int,
        *,
        start_frame: int = 0,
        end_frame: int | None = None,
        repeats: int = 0,
        fade_in_frames: int = 0,
        end_fade_frames: int = 0,
        gain: float = 1.0,
        tag: object = None,
        begin_frame: int | None = None,
        loop_start_frame: int | None = None,
        loop_end_frame: int | None = None,
        first_full: bool = True,
        outro: bool = False,
        loop_fade_in_frames: int = 0,
        loop_fade_out_frames: int = 0,
        loop_fade_mode: str = JUMP,
    ) -> None:
        if buffer.ndim != 2 or buffer.shape[1] != 2:
            raise ValueError("buffer must have shape (frames, 2)")
        total = buffer.shape[0]
        end_frame = total if end_frame is None else end_frame
        shape = Shape(
            start_frame=start_frame,
            end_frame=end_frame,
            loop_start_frame=start_frame if loop_start_frame is None else loop_start_frame,
            loop_end_frame=end_frame if loop_end_frame is None else loop_end_frame,
            first_full=first_full,
            outro=outro,
            repeats=repeats,
            fade_in_frames=fade_in_frames,
            end_fade_frames=end_fade_frames,
            loop_fade_in_frames=loop_fade_in_frames,
            loop_fade_out_frames=loop_fade_out_frames,
            loop_fade_mode=loop_fade_mode,
        )
        if (problem := shape.problem(total)) is not None:
            raise ValueError(problem)
        first_end = end_frame if first_full else shape.loop_end_frame
        begin_frame = start_frame if begin_frame is None else begin_frame
        if not start_frame <= begin_frame < first_end:
            raise ValueError(f"begin {begin_frame} outside [{start_frame}, {first_end})")

        self.voice_id = voice_id
        self.tag = tag  # opaque link back to the cue, for the GUI
        self.buffer = buffer
        self.samplerate = samplerate
        self._set_shape(shape)

        # Playback state (written by the audio thread only).
        self.position = begin_frame  # the first segment may begin mid-way (preview)
        self.pass_index = 0  # 0 during the first segment, then +1 per loop pass
        self.phase = FIRST
        self.seg_start = start_frame
        self.seg_end = first_end
        self.frames_played = 0
        self.devamped = False
        self.stopping = False
        self.finished = False
        self.ended_naturally = False  # reached its end (vs. stopped): triggers auto-follow
        self.last_produced = 0  # content frames written by the last render() call

        self._gain = GainRamp(gain, seconds_to_frames(GAIN_RAMP_SECONDS, samplerate))
        self._min_stop_frames = max(1, seconds_to_frames(MIN_STOP_SECONDS, samplerate))
        self._stop_total = 0
        self._stop_done = 0
        self._fade_start: int | None = None
        self._lf_in = False  # this segment fades in from its start (loop fade-in)
        self._lf_out_start: int | None = None  # where this segment's loop fade-out begins
        self._xf_start: int | None = None  # where this segment's crossfade begins
        self._tail_started = False  # the loop fade-out / crossfade has begun
        self._dc_tail: np.ndarray | None = None  # old audio fading out after a live jump
        self._dc_done = 0
        self._end_after_dc = False  # a live edit ended the voice: play the tail, finish
        self._plan_tail()

    def _set_shape(self, shape: Shape) -> None:
        self.shape = shape
        self.start_frame = shape.start_frame
        self.end_frame = shape.end_frame
        self.loop_start_frame = shape.loop_start_frame
        self.loop_end_frame = shape.loop_end_frame
        self.first_end_frame = shape.end_frame if shape.first_full else shape.loop_end_frame
        self.outro = shape.outro and shape.loop_end_frame < shape.end_frame
        self.repeats = shape.repeats
        self.fade_in_frames = max(0, shape.fade_in_frames)
        self.end_fade_frames = max(0, shape.end_fade_frames)
        self.loop_fade_in_frames = max(0, shape.loop_fade_in_frames) if shape.repeats else 0
        self.loop_fade_out_frames = max(0, shape.loop_fade_out_frames) if shape.repeats else 0
        self.loop_fade_mode = shape.loop_fade_mode

    # ----------------------------------------------------------------- state queries
    @property
    def is_infinite(self) -> bool:
        return self.repeats == INFINITE

    @property
    def is_looping(self) -> bool:
        """True while an infinite voice is still set to loop (not devamped, not stopping)."""
        return self.is_infinite and not self.devamped and not self.stopping and not self.finished

    @property
    def is_final_pass(self) -> bool:
        """True while playing the last segment (nothing follows it)."""
        return self._next_segment() is None

    def remaining_frames(self) -> int | None:
        """Frames left until the natural end, or None while looping forever."""
        if self.finished:
            return 0
        if self.is_looping:
            return None
        left = self.seg_end - self.position
        if self.phase == OUTRO:
            return left
        if self.is_infinite:
            loops = 1 if self._jump_committed else 0
        else:
            loops = max(0, self.repeats - self.pass_index)
        left += loops * (self.loop_end_frame - self.loop_start_frame)
        last_end = self.loop_end_frame if loops else self.seg_end
        if self.outro and last_end == self.loop_end_frame:
            left += self.end_frame - self.loop_end_frame
        return left

    @property
    def gain(self) -> float:
        return self._gain.target

    # ----------------------------------------------------------------- controls
    def set_gain(self, gain: float) -> None:
        self._gain.set_target(gain)

    def devamp(self) -> None:
        """Let an infinite voice finish its current pass, then stop. No-op otherwise."""
        if not self.is_looping:
            return
        self.devamped = True
        self._plan_tail()  # the end fade now applies if this segment became the last

    def reshape(self, shape: Shape) -> None:
        """Live edit (audio thread): apply new trims / fades / repeats / loop settings to
        this playing voice. An invalid shape is ignored."""
        if self.finished or self._end_after_dc or shape.problem(len(self.buffer)) is not None:
            return
        self._set_shape(shape)
        if self.phase == FIRST:
            bounds: tuple[int, int] | None = (self.start_frame, self.first_end_frame)
        elif self.phase == LOOP:
            bounds = (self.loop_start_frame, self.loop_end_frame)
        else:
            bounds = (self.loop_end_frame, self.end_frame) if self.outro else None
        self._tail_started = False
        audible = self.frames_played > 0
        if not audible and self.phase == FIRST:
            self.position = self.start_frame  # not started yet: begin at the new Start
        if bounds is None or self.position >= bounds[1]:
            # Past the (new) end of this segment: as if it had just been reached.
            if audible:
                self._declick_from(self.position)
            if bounds is not None:
                self.seg_start, self.seg_end = bounds
            nxt = None if bounds is None else self._next_segment()
            if nxt is None:
                if audible:
                    self._end_after_dc = True
                else:
                    self.finished = True
                    self.ended_naturally = True
                return
            self._goto(nxt, dipped=False)
            return
        if self.position < bounds[0]:
            if audible:
                self._declick_from(self.position)
            self.position = bounds[0]
        self.seg_start, self.seg_end = bounds
        self._plan_tail()

    def reshaped(self, **changes: object) -> Shape:
        """The current shape with some fields changed (handy for tests)."""
        return replace(self.shape, **changes)

    def stop(self, fade_frames: int) -> None:
        """Fade out from the current point and finish. Repeated calls are ignored."""
        if self.finished or self.stopping:
            return
        self.stopping = True
        self._stop_total = max(self._min_stop_frames, int(fade_frames))
        self._stop_done = 0

    # ----------------------------------------------------------------- rendering
    def render(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, 2), dtype=np.float32)
        if self.finished or frames <= 0:
            return out

        env = np.ones(frames, dtype=np.float32)
        produced = 0
        content_done = False
        if self._end_after_dc:
            produced = frames  # content was cut by a live edit: only the de-click tail
        while produced < frames:
            seg = min(frames - produced, self.seg_end - self.position)
            pos = self.position
            out[produced : produced + seg] = self.buffer[pos : pos + seg]
            self._apply_fades(env[produced : produced + seg], pos, seg)
            if self._xf_start is not None:
                self._crossfade(out[produced : produced + seg], pos, seg)
            self.position += seg
            produced += seg
            if self.position >= self.seg_end:
                nxt = self._next_segment()
                if nxt is None:
                    content_done = True
                    break
                self._goto(nxt, dipped=self._lf_out_start is not None)

        out *= env[:, None]
        if self._dc_tail is not None:
            produced = self._mix_declick(out, produced)
            if self._end_after_dc:
                content_done = self._dc_tail is None
        gain = self._gain.next_block(frames)
        if self.stopping:
            gain = gain * self._stop_envelope(frames)
        if isinstance(gain, np.ndarray):
            out *= gain[:, None]
        elif gain != 1.0:
            out *= gain

        self.frames_played += produced
        self.last_produced = produced
        if content_done or (self.stopping and self._stop_done >= self._stop_total):
            self.finished = True
            self.ended_naturally = content_done and not self.stopping
        return out

    # ----------------------------------------------------------------- internals
    def _goto(self, nxt: tuple[str, int, int], dipped: bool) -> None:
        self.phase, self.seg_start, self.seg_end = nxt
        if self.phase == LOOP:
            self.pass_index += 1
        self.position = self.seg_start
        self._enter_segment(dipped)

    def _declick_from(self, pos: int) -> None:
        """A live edit makes playback jump away from `pos`: keep the next few ms of the
        old audio to fade out under the new audio fading in."""
        n = self._min_stop_frames
        tail = np.zeros((n, 2), dtype=np.float32)
        avail = max(0, min(n, len(self.buffer) - pos))
        tail[:avail] = self.buffer[pos : pos + avail]
        t = np.arange(1, n + 1, dtype=np.float64) / n
        tail *= fade_out_curve(t)[:, None]
        self._dc_tail = tail
        self._dc_done = 0

    def _mix_declick(self, out: np.ndarray, produced: int) -> int:
        tail = self._dc_tail
        assert tail is not None
        n = len(tail)
        m = min(len(out), n - self._dc_done)
        k = np.arange(self._dc_done + 1, self._dc_done + m + 1, dtype=np.float64) / n
        if self._end_after_dc:
            out[:m] = 0.0
            produced = m
        else:
            out[:m] *= fade_in_curve(k)[:, None]
        out[:m] += tail[self._dc_done : self._dc_done + m]
        self._dc_done += m
        if self._dc_done >= n:
            self._dc_tail = None
        return produced

    @property
    def _jump_committed(self) -> bool:
        """A crossfade into the next pass has begun, so that jump must happen."""
        return self._tail_started and self._xf_start is not None

    def _next_segment(self) -> tuple[str, int, int] | None:
        """The segment that follows the current one, given the repeat state now."""
        if self.phase == OUTRO:
            return None
        if self.is_infinite:
            more = not self.devamped or self._jump_committed
        else:
            more = self.pass_index < self.repeats
        if more:
            return LOOP, self.loop_start_frame, self.loop_end_frame
        if self.outro and self.seg_end == self.loop_end_frame:
            return OUTRO, self.loop_end_frame, self.end_frame
        return None

    def _enter_segment(self, after_dip: bool) -> None:
        """Called when playback moves into a new segment (it is at its start)."""
        self._tail_started = False
        self._lf_out_start = self._xf_start = None
        self._lf_in = (
            self.loop_fade_in_frames > 0
            and self.loop_fade_mode != CROSSFADE
            and (self.phase == LOOP or (self.phase == OUTRO and after_dip))
        )
        self._plan_tail()

    def _plan_tail(self) -> None:
        """Work out how this segment ends (end fade, loop fade-out, crossfade) from the
        repeat state now. Called on entering a segment and after devamp()."""
        now = self.position
        self._fade_start = self._end_fade_start(now) if self.is_final_pass else None
        if self._tail_started:
            return  # a loop fade-out or crossfade under way always completes
        self._lf_out_start = self._xf_start = None
        length = self.loop_fade_out_frames
        if length <= 0:
            return
        nxt = self._next_segment()
        jumps = nxt is not None and nxt[0] == LOOP
        seg_len = self.seg_end - self.seg_start
        mode = self.loop_fade_mode
        if mode == CROSSFADE:
            n = min(length, self.loop_start_frame, seg_len)
            if jumps and n > 0:
                self._xf_start = max(self.seg_end - n, now)
            return
        at_loop_end = self.seg_end == self.loop_end_frame
        if mode == JUMP:
            dip = jumps
        elif mode == LOOP_END:
            dip = jumps and at_loop_end
        else:  # PASS
            dip = jumps or at_loop_end
        if dip:
            self._lf_out_start = max(self.seg_end - min(length, seg_len), now)

    def _end_fade_start(self, now: int) -> int | None:
        if self.end_fade_frames <= 0:
            return None
        return max(self.seg_end - self.end_fade_frames, now)

    def _apply_fades(self, env: np.ndarray, pos: int, seg: int) -> None:
        if (
            self.phase == FIRST
            and self.fade_in_frames
            and pos < self.start_frame + self.fade_in_frames
        ):
            idx = np.arange(pos, pos + seg, dtype=np.float64)
            t = np.clip((idx - self.start_frame) / self.fade_in_frames, 0.0, 1.0)
            env *= fade_in_curve(t)
        fs = self._fade_start
        if fs is not None and pos + seg > fs:
            idx = np.arange(pos, pos + seg, dtype=np.float64)
            length = self.seg_end - fs
            # +1 so the last frame of the pass lands exactly on silence.
            t = np.clip((idx - fs + 1) / length, 0.0, 1.0)
            env *= np.where(idx >= fs, fade_out_curve(t), 1.0).astype(np.float32)
        if self._lf_in:
            n = min(self.loop_fade_in_frames, self.seg_end - self.seg_start)
            if pos < self.seg_start + n:
                idx = np.arange(pos, pos + seg, dtype=np.float64)
                t = np.clip((idx - self.seg_start) / n, 0.0, 1.0)
                env *= fade_in_curve(t)
        ls = self._lf_out_start
        if ls is not None and pos + seg > ls:
            self._tail_started = True
            idx = np.arange(pos, pos + seg, dtype=np.float64)
            t = np.clip((idx - ls + 1) / (self.seg_end - ls), 0.0, 1.0)
            env *= np.where(idx >= ls, fade_out_curve(t), 1.0).astype(np.float32)

    def _crossfade(self, out: np.ndarray, pos: int, seg: int) -> None:
        """Blend the tail of this segment with the audio leading into Loop start, so that
        at the jump the pre-roll has reached Loop start at full level."""
        xs = self._xf_start
        assert xs is not None
        a = max(pos, xs)
        n = pos + seg - a
        if n <= 0:
            return
        self._tail_started = True
        idx = np.arange(a, a + n, dtype=np.float64)
        t = np.clip((idx - xs + 1) / (self.seg_end - xs), 0.0, 1.0)
        pre = a - self.seg_end + self.loop_start_frame
        k = a - pos
        out[k : k + n] = (
            out[k : k + n] * fade_out_curve(t)[:, None]
            + self.buffer[pre : pre + n] * fade_in_curve(t)[:, None]
        )

    def _stop_envelope(self, frames: int) -> np.ndarray:
        k = np.arange(self._stop_done + 1, self._stop_done + frames + 1, dtype=np.float64)
        t = np.clip(k / self._stop_total, 0.0, 1.0)
        self._stop_done = min(self._stop_total, self._stop_done + frames)
        return fade_out_curve(t)
