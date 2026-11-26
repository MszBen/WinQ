"""Turns cues into Voices: the bridge between the model (seconds, dB) and the engine
(frames, linear gain). Also owns the decoded-audio cache.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, fields
from pathlib import Path

from winq.audio.dsp import db_to_gain, seconds_to_frames
from winq.audio.engine import AudioEngine
from winq.audio.loader import AudioLoadError, LoadedAudio, load_audio
from winq.audio.voice import Shape, Voice
from winq.model.cue import AFTER_OUTRO, FIRST_INTRO, MIN_LEVEL_DB, AudioCue


def level_to_gain(level_db: float) -> float:
    return 0.0 if level_db <= MIN_LEVEL_DB else db_to_gain(level_db)


def cue_shape(cue: AudioCue, sr: int, total: int) -> Shape:
    """The cue's trims, fades, repeats and loop settings in frames, kept valid for a
    buffer of `total` frames at `sr`."""
    start = min(seconds_to_frames(cue.start_time, sr), total - 1)
    end = total if cue.end_time is None else seconds_to_frames(cue.end_time, sr)
    end = max(start + 1, min(end, total))
    loop_start, loop_end, first_full, outro = start, end, True, False
    section = cue.section_bounds()
    if section is not None:
        loop_start = min(max(seconds_to_frames(section[0], sr), start), end - 1)
        loop_end = min(max(seconds_to_frames(section[1], sr), loop_start + 1), end)
        first_full = cue.loop_first != FIRST_INTRO
        outro = cue.loop_after == AFTER_OUTRO
    return Shape(
        start_frame=start,
        end_frame=end,
        loop_start_frame=loop_start,
        loop_end_frame=loop_end,
        first_full=first_full,
        outro=outro,
        repeats=cue.repeats,
        fade_in_frames=seconds_to_frames(cue.fade_in, sr),
        end_fade_frames=seconds_to_frames(cue.end_fade, sr),
        loop_fade_in_frames=seconds_to_frames(cue.loop_fade_in, sr),
        loop_fade_out_frames=seconds_to_frames(cue.loop_fade_out, sr),
        loop_fade_mode=cue.loop_fade_mode,
    )


class AudioCache:
    """Decoded audio by path. Files decode on background threads (`prefetch`); `get`
    waits for a pending decode, or decodes on the spot if it was never requested."""

    def __init__(self, samplerate: int, workers: int = 2) -> None:
        self.samplerate = samplerate
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="winq-load")
        self._lock = threading.Lock()
        self._entries: dict[Path, Future[LoadedAudio]] = {}

    def prefetch(self, paths: list[Path]) -> None:
        with self._lock:
            for p in paths:
                if p not in self._entries:
                    self._entries[p] = self._pool.submit(load_audio, p, self.samplerate)

    def get(self, path: Path) -> LoadedAudio | None:
        with self._lock:
            fut = self._entries.get(path)
            if fut is None or (fut.done() and fut.exception() is not None):
                # Not requested yet, or failed before (the file may have come back): retry.
                fut = self._pool.submit(load_audio, path, self.samplerate)
                self._entries[path] = fut
        try:
            return fut.result()
        except AudioLoadError:
            return None

    def peek(self, path: Path) -> LoadedAudio | None:
        """Decoded audio if it is already loaded; never waits or starts a decode."""
        with self._lock:
            fut = self._entries.get(path)
        if fut is None or not fut.done() or fut.exception() is not None:
            return None
        return fut.result()

    def is_ready(self, path: Path) -> bool:
        with self._lock:
            fut = self._entries.get(path)
        return fut is not None and fut.done()

    def retain(self, paths: set[Path]) -> None:
        """Drop everything not in `paths` (call when a show is replaced)."""
        with self._lock:
            for p in [p for p in self._entries if p not in paths]:
                del self._entries[p]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True)
class Progress:
    elapsed: float  # seconds since the instance started
    pass_fraction: float  # 0..1 through the current pass
    remaining: float | None  # seconds left, None while looping forever


class CuePlayer:
    """Implements `winq.control.transport.Player` on top of the AudioEngine."""

    def __init__(self, engine: AudioEngine, cache: AudioCache) -> None:
        self.engine = engine
        self.cache = cache
        self._voices: dict[int, Voice] = {}

    def start(
        self,
        cue: AudioCue,
        *,
        delay: float = 0.0,
        after: int | None = None,
        parent: int | None = None,
    ) -> int | None:
        """Start a new copy of `cue`, now or scheduled (see engine.Mixer: `after` = when
        that voice ends naturally, `parent` = cancelled if that voice is stopped first)."""
        voice = self._make_voice(cue)
        if voice is None:
            return None
        self._voices[voice.voice_id] = voice
        self.engine.play(voice, delay_seconds=delay, after=after, parent=parent)
        return voice.voice_id

    def start_now(self, voice_id: int) -> None:
        self.engine.start_now(voice_id)

    def cancel(self, voice_id: int) -> None:
        self.engine.cancel(voice_id)

    def preview(self, cue: AudioCue, from_seconds: float) -> int | None:
        """Audition `cue` from a point inside its trim. Same as a GO-started copy (level,
        fades, repeats) except where it begins. The Transport doesn't know about it."""
        voice = self._make_voice(cue, from_seconds)
        if voice is None:
            return None
        self._voices[voice.voice_id] = voice
        self.engine.play(voice)
        return voice.voice_id

    def position(self, voice_id: int) -> float | None:
        """Current file position in seconds of a playing voice (for waveform playheads)."""
        v = self._voices.get(voice_id)
        return None if v is None or v.finished else v.position / v.samplerate

    def reshape(self, voice_id: int, cue: AudioCue) -> None:
        """Live edit: give a playing (or waiting) copy of `cue` its current trims, fades,
        repeats and loop settings. Skipped if the cue now points at another file."""
        v = self._voices.get(voice_id)
        audio = None if cue.is_missing else self.cache.peek(cue.file_path)
        if v is None or v.finished or audio is None or audio.data is not v.buffer:
            return
        self.engine.reshape(voice_id, cue_shape(cue, audio.samplerate, audio.frames))

    def _make_voice(self, cue: AudioCue, from_seconds: float | None = None) -> Voice | None:
        if cue.is_missing:
            return None
        audio = self.cache.get(cue.file_path)
        if audio is None:
            return None
        sr = audio.samplerate
        shape = cue_shape(cue, sr, audio.frames)
        begin = None
        if from_seconds is not None:
            # Preview starts inside the first play (for "intro", that ends at Loop end).
            first_end = shape.end_frame if shape.first_full else shape.loop_end_frame
            begin = seconds_to_frames(from_seconds, sr)
            begin = min(max(begin, shape.start_frame), first_end - 1)
        return Voice(
            self.engine.next_voice_id(),
            audio.data,
            sr,
            gain=level_to_gain(cue.level_db),
            tag=cue.id,
            begin_frame=begin,
            **{f.name: getattr(shape, f.name) for f in fields(Shape)},
        )

    def devamp(self, voice_id: int) -> None:
        self.engine.devamp(voice_id)

    def stop(self, voice_id: int, fade_seconds: float) -> None:
        self.engine.stop(voice_id, fade_seconds)

    def stop_all(self, fade_seconds: float) -> None:
        self.engine.stop_all(fade_seconds)

    def set_level(self, voice_id: int, level_db: float) -> None:
        self.engine.set_gain(voice_id, level_to_gain(level_db))

    def forget(self, voice_id: int) -> None:
        self._voices.pop(voice_id, None)

    def progress(self, voice_id: int) -> Progress | None:
        """Read-only snapshot of a voice for display (safe from the GUI thread)."""
        v = self._voices.get(voice_id)
        if v is None:
            return None
        sr = v.samplerate
        # Read once: the audio thread may move to the next segment meanwhile.
        seg_start, seg_end, position = v.seg_start, v.seg_end, v.position
        pass_len = max(1, seg_end - seg_start)
        into_pass = min(max(position - seg_start, 0), pass_len)
        if v.stopping:
            left = (pass_len - into_pass) / sr
            remaining = None if v.is_infinite and not v.devamped else left
        else:
            frames = v.remaining_frames()
            remaining = None if frames is None else max(0, frames) / sr
        return Progress(
            elapsed=v.frames_played / sr,
            pass_fraction=into_pass / pass_len,
            remaining=remaining,
        )
