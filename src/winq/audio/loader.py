"""Decode audio files into RAM at the engine's sample rate, and compute waveform peaks.

Runs on a worker thread (never in the audio callback). Output buffers are float32,
shape (frames, 2), C-contiguous: exactly what `Voice` expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

SUPPORTED_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff"})
PEAK_BUCKET_FRAMES = 256  # one waveform min/max pair per this many frames


class AudioLoadError(Exception):
    pass


@dataclass(frozen=True)
class LoadedAudio:
    path: Path
    data: np.ndarray  # float32, (frames, 2), at `samplerate`
    samplerate: int
    source_samplerate: int
    source_channels: int
    peaks: np.ndarray  # float32, (buckets, 2): [min, max] of the mono mix per bucket

    @property
    def frames(self) -> int:
        return self.data.shape[0]

    @property
    def duration(self) -> float:
        return self.frames / self.samplerate


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def to_stereo(data: np.ndarray) -> np.ndarray:
    """Mono is duplicated to both sides; more than two channels keep the first two."""
    if data.shape[1] == 1:
        return np.repeat(data, 2, axis=1)
    return data[:, :2]


def compute_peaks(data: np.ndarray, bucket: int = PEAK_BUCKET_FRAMES) -> np.ndarray:
    mono = data.mean(axis=1)
    n_buckets = -(-len(mono) // bucket)  # ceil division
    if n_buckets == 0:
        return np.zeros((0, 2), dtype=np.float32)
    padded = np.zeros(n_buckets * bucket, dtype=np.float32)
    padded[: len(mono)] = mono
    blocks = padded.reshape(n_buckets, bucket)
    return np.stack([blocks.min(axis=1), blocks.max(axis=1)], axis=1).astype(np.float32)


def probe_duration(path: str | Path) -> float | None:
    """File length in seconds from its header (fast, no decode), or None if unreadable."""
    path = Path(path)
    if not is_supported(path):
        return None
    try:
        info = sf.info(path)
    except (sf.LibsndfileError, RuntimeError, OSError):
        return None
    return float(info.duration) if info.frames > 0 else None


def load_audio(path: str | Path, target_samplerate: int) -> LoadedAudio:
    path = Path(path)
    if not is_supported(path):
        raise AudioLoadError(f"Unsupported file type: {path.suffix or '(none)'}")
    try:
        raw, source_sr = sf.read(path, dtype="float32", always_2d=True)
    except MemoryError as exc:
        raise AudioLoadError(f"{path.name} is too large to load into memory") from exc
    except Exception as exc:  # LibsndfileError, OSError, corrupt headers, odd codecs...
        raise AudioLoadError(f"Could not read {path.name}: {exc}") from exc
    if raw.shape[0] == 0:
        raise AudioLoadError(f"{path.name} contains no audio")

    try:
        source_channels = raw.shape[1]
        data = to_stereo(raw)
        del raw  # free the multichannel copy early (large files)
        if source_sr != target_samplerate:
            data = soxr.resample(data, source_sr, target_samplerate, quality="HQ")
        data = np.ascontiguousarray(data, dtype=np.float32)
        peaks = compute_peaks(data)
    except MemoryError as exc:
        raise AudioLoadError(f"{path.name} is too large to load into memory") from exc

    return LoadedAudio(
        path=path,
        data=data,
        samplerate=target_samplerate,
        source_samplerate=source_sr,
        source_channels=source_channels,
        peaks=peaks,
    )
