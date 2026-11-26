"""The audio engine: one output stream that mixes every playing Voice.

`Mixer` is pure (no audio device). It is what the tests drive, and what the stream
callback calls. `AudioEngine` wraps a Mixer in a `sounddevice.OutputStream`.

Threading: the GUI sends commands with `send()` / the helper methods. The audio thread
drains them at the start of each block. Events (voice started/ended/cancelled) flow
back through `poll_events()`, which the GUI calls from a QTimer.

Scheduled starts (auto-follow / auto-continue) are sample-accurate. A voice can be
*pending*:
- `delay_frames` after the Play command arrives, or
- `after` another voice: it starts `delay_frames` after that voice **ends naturally**,
  on the exact sample, even mid-block. If that voice is stopped instead, the pending
  voice is cancelled.
- `parent`: the voice is cancelled if the parent is stopped before it starts (used for
  "continue after a delay").
Cancelling a pending voice also cancels everything waiting on it.
"""

from __future__ import annotations

import itertools
import queue
from collections import deque
from dataclasses import dataclass

import numpy as np

from winq.audio.dsp import GAIN_RAMP_SECONDS, GainRamp, seconds_to_frames
from winq.audio.voice import Shape, Voice

CHANNELS = 2


# --------------------------------------------------------------------- commands
@dataclass(frozen=True, slots=True)
class Play:
    voice: Voice
    delay_frames: int = 0
    after: int | None = None  # start when this voice ends naturally (+ delay)
    parent: int | None = None  # cancel if this voice is stopped before we start


@dataclass(frozen=True, slots=True)
class StartNow:
    """Start a pending voice at the next block, ignoring what it was waiting for."""

    voice_id: int


@dataclass(frozen=True, slots=True)
class Cancel:
    """Drop a pending voice (and everything waiting on it). Ignored once it's playing."""

    voice_id: int


@dataclass(frozen=True, slots=True)
class SetGain:
    voice_id: int
    gain: float


@dataclass(frozen=True, slots=True)
class Reshape:
    """Live edit of a playing (or pending) voice's trims, fades and loop settings."""

    voice_id: int
    shape: Shape


@dataclass(frozen=True, slots=True)
class Devamp:
    voice_id: int


@dataclass(frozen=True, slots=True)
class Stop:
    voice_id: int
    fade_frames: int


@dataclass(frozen=True, slots=True)
class StopAll:
    fade_frames: int


@dataclass(frozen=True, slots=True)
class SetMaster:
    gain: float


Command = Play | StartNow | Cancel | SetGain | Reshape | Devamp | Stop | StopAll | SetMaster


# --------------------------------------------------------------------- events
@dataclass(frozen=True, slots=True)
class VoiceStarted:
    voice_id: int


@dataclass(frozen=True, slots=True)
class VoiceEnded:
    voice_id: int
    natural: bool = True  # reached its end (False: stopped)


@dataclass(frozen=True, slots=True)
class VoiceCancelled:
    voice_id: int  # a pending voice that will never start


Event = VoiceStarted | VoiceEnded | VoiceCancelled


@dataclass(slots=True)
class _Pending:
    voice: Voice
    start_at: int | None  # absolute frame; None while waiting for `after`
    delay: int
    after: int | None
    parent: int | None


# --------------------------------------------------------------------- mixer
class Mixer:
    def __init__(self, samplerate: int) -> None:
        self.samplerate = samplerate
        self._commands: queue.SimpleQueue[Command] = queue.SimpleQueue()
        self._events: queue.SimpleQueue[Event] = queue.SimpleQueue()
        self._voices: dict[int, Voice] = {}  # playing (audio-thread owned)
        self._pending: dict[int, _Pending] = {}  # scheduled, not started yet
        self._clock = 0  # frames rendered so far
        self._master = GainRamp(1.0, seconds_to_frames(GAIN_RAMP_SECONDS, samplerate))
        self.peak = 0.0  # last block's output peak
        self.voice_errors = 0  # voices dropped because their render raised
        self._peak_max = np.zeros(CHANNELS, dtype=np.float32)  # per channel, since last take

    def take_peaks(self) -> tuple[float, float]:
        """Highest per-channel output level since the last call (for the meter). Called
        from the GUI thread; a block racing with this call may be missed, which is
        harmless for a display."""
        peaks = self._peak_max
        self._peak_max = np.zeros(CHANNELS, dtype=np.float32)
        return float(peaks[0]), float(peaks[1])

    # GUI side ---------------------------------------------------------
    def send(self, command: Command) -> None:
        self._commands.put(command)

    def poll_events(self) -> list[Event]:
        events: list[Event] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    # Audio side -------------------------------------------------------
    def render(self, frames: int) -> np.ndarray:
        self._drain_commands()
        out = np.zeros((frames, CHANNELS), dtype=np.float32)
        block_end = self._clock + frames

        # (offset in block, voice id): voices to render this block. A voice that starts
        # mid-block renders only from its offset on.
        work: deque[tuple[int, int]] = deque((0, vid) for vid in self._voices)
        for vid in self._due_pending(block_end):
            work.append(self._activate(vid))
        while work:
            offset, vid = work.popleft()
            voice = self._voices[vid]
            try:
                out[offset:] += voice.render(frames - offset)
            except Exception:
                # A bug in one voice must never silence the whole show: drop that voice
                # (as if stopped, so nothing follows it) and keep mixing the others.
                self.voice_errors += 1
                voice.finished = True
                voice.ended_naturally = False
                voice.last_produced = 0
            if voice.finished:
                end_abs = self._clock + offset + voice.last_produced
                work.extend(self._finish(vid, voice.ended_naturally, end_abs, block_end))
        self._clock = block_end

        master = self._master.next_block(frames)
        if isinstance(master, np.ndarray):
            out *= master[:, None]
        elif master != 1.0:
            out *= master
        np.clip(out, -1.0, 1.0, out=out)
        if frames:
            block_peaks = np.abs(out).max(axis=0)
            self.peak = float(block_peaks.max())
            self._peak_max = np.maximum(self._peak_max, block_peaks)
        else:
            self.peak = 0.0
        return out

    @property
    def active_voice_count(self) -> int:
        return len(self._voices)

    @property
    def pending_voice_count(self) -> int:
        return len(self._pending)

    # scheduling ---------------------------------------------------------
    def _due_pending(self, block_end: int) -> list[int]:
        due = [
            vid
            for vid, p in self._pending.items()
            if p.start_at is not None and p.start_at < block_end
        ]
        return sorted(due, key=lambda vid: self._pending[vid].start_at)

    def _activate(self, vid: int) -> tuple[int, int]:
        p = self._pending.pop(vid)
        self._voices[vid] = p.voice
        self._events.put(VoiceStarted(vid))
        return max(0, p.start_at - self._clock), vid

    def _finish(
        self, vid: int, natural: bool, end_abs: int, block_end: int
    ) -> list[tuple[int, int]]:
        """A voice ended. Release (natural) or cancel (stopped) whatever waits on it.
        Returns voices that must start later in this same block."""
        del self._voices[vid]
        self._events.put(VoiceEnded(vid, natural))
        started: list[tuple[int, int]] = []
        for pid, p in list(self._pending.items()):
            if pid not in self._pending:
                continue  # cancelled earlier in this loop
            if p.after == vid:
                if natural:
                    p.after = None
                    p.start_at = end_abs + p.delay
                    if p.start_at < block_end:
                        started.append(self._activate(pid))
                else:
                    self._cancel(pid)
            elif p.parent == vid and not natural:
                self._cancel(pid)
        return started

    def _cancel(self, vid: int) -> None:
        if self._pending.pop(vid, None) is None:
            return
        self._events.put(VoiceCancelled(vid))
        for pid, p in list(self._pending.items()):
            if p.after == vid or p.parent == vid:
                self._cancel(pid)

    def _cancel_dependents(self, vid: int) -> None:
        """A voice is being stopped: nothing waiting on it may start any more."""
        for pid, p in list(self._pending.items()):
            if pid in self._pending and (p.after == vid or p.parent == vid):
                self._cancel(pid)

    # commands -----------------------------------------------------------
    def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            self._apply(cmd)

    def _apply(self, cmd: Command) -> None:
        match cmd:
            case Play(voice, delay, after, parent):
                waiting_on_gone = after is not None and after not in self._voices
                waiting_on_gone = waiting_on_gone and after not in self._pending
                if waiting_on_gone:
                    # What it follows already ended (or never existed): never starts.
                    self._events.put(VoiceCancelled(voice.voice_id))
                    return
                start_at = None if after is not None else self._clock + max(0, delay)
                self._pending[voice.voice_id] = _Pending(
                    voice, start_at, max(0, delay), after, parent
                )
            case StartNow(voice_id):
                if (p := self._pending.get(voice_id)) is not None:
                    p.after = None
                    p.parent = None
                    p.start_at = self._clock
            case Cancel(voice_id):
                self._cancel(voice_id)
            case SetGain(voice_id, gain):
                v = self._voices.get(voice_id) or self._pending_voice(voice_id)
                if v is not None:
                    v.set_gain(gain)
            case Reshape(voice_id, shape):
                v = self._voices.get(voice_id) or self._pending_voice(voice_id)
                if v is not None:
                    v.reshape(shape)
            case Devamp(voice_id):
                if (v := self._voices.get(voice_id)) is not None:
                    v.devamp()
            case Stop(voice_id, fade_frames):
                if (v := self._voices.get(voice_id)) is not None:
                    self._cancel_dependents(voice_id)
                    v.stop(fade_frames)
                else:
                    self._cancel(voice_id)
            case StopAll(fade_frames):
                for vid in list(self._pending):
                    self._cancel(vid)
                for v in self._voices.values():
                    v.stop(fade_frames)
            case SetMaster(gain):
                self._master.set_target(gain)

    def _pending_voice(self, voice_id: int) -> Voice | None:
        p = self._pending.get(voice_id)
        return None if p is None else p.voice


# --------------------------------------------------------------------- engine
class AudioEngine:
    """Owns the output stream. Create on the GUI thread; call `start()` to open the device."""

    def __init__(self, device: int | None = None, *, latency: str | float = "low") -> None:
        import sounddevice as sd

        from winq.audio.devices import preferred_output_device_index

        self._sd = sd
        self.device_index = preferred_output_device_index() if device is None else device
        info = sd.query_devices(self.device_index)
        self.device_name: str = info["name"]
        self.host_api: str = sd.query_hostapis(info["hostapi"])["name"]
        self.samplerate = int(info["default_samplerate"])
        self.latency = latency
        self.mixer = Mixer(self.samplerate)
        self.callbacks = 0
        self.xruns = 0  # under/overflows reported by PortAudio
        self.errors = 0  # blocks lost to an exception in the mixer
        self._stream = None
        self._ids = itertools.count(1)

    # lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = self._sd.OutputStream(
            device=self.device_index,
            samplerate=self.samplerate,
            channels=CHANNELS,
            dtype="float32",
            latency=self.latency,
            callback=self._callback,
        )
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> AudioEngine:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def output_latency(self) -> float | None:
        return None if self._stream is None else float(self._stream.latency)

    # control (GUI thread) ---------------------------------------------
    def next_voice_id(self) -> int:
        return next(self._ids)

    def play(
        self,
        voice: Voice,
        *,
        delay_seconds: float = 0.0,
        after: int | None = None,
        parent: int | None = None,
    ) -> None:
        delay = seconds_to_frames(delay_seconds, self.samplerate)
        self.mixer.send(Play(voice, delay, after, parent))

    def start_now(self, voice_id: int) -> None:
        self.mixer.send(StartNow(voice_id))

    def cancel(self, voice_id: int) -> None:
        self.mixer.send(Cancel(voice_id))

    def set_gain(self, voice_id: int, gain: float) -> None:
        self.mixer.send(SetGain(voice_id, gain))

    def reshape(self, voice_id: int, shape: Shape) -> None:
        self.mixer.send(Reshape(voice_id, shape))

    def devamp(self, voice_id: int) -> None:
        self.mixer.send(Devamp(voice_id))

    def stop(self, voice_id: int, fade_seconds: float) -> None:
        self.mixer.send(Stop(voice_id, seconds_to_frames(fade_seconds, self.samplerate)))

    def stop_all(self, fade_seconds: float) -> None:
        self.mixer.send(StopAll(seconds_to_frames(fade_seconds, self.samplerate)))

    def set_master(self, gain: float) -> None:
        self.mixer.send(SetMaster(gain))

    def poll_events(self) -> list[Event]:
        return self.mixer.poll_events()

    # audio thread -----------------------------------------------------
    def _callback(self, outdata: np.ndarray, frames: int, _time: object, status: object) -> None:
        self.callbacks += 1
        if status:
            self.xruns += 1
        try:
            outdata[:] = self.mixer.render(frames)
        except Exception:
            # Never let an exception kill the stream (PortAudio would stop calling us and
            # the rest of the show would be silent). Output silence for this block.
            self.errors += 1
            outdata.fill(0)

    @property
    def is_open(self) -> bool:
        """True after start() (the device was opened), until close()."""
        return self._stream is not None

    @property
    def is_active(self) -> bool:
        """True while the stream is open and running (False after a device error)."""
        return self._stream is not None and bool(self._stream.active)
