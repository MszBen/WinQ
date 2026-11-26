"""Loader (decode → stereo → resample → peaks) and dsp helper tests."""

from __future__ import annotations

import math

import numpy as np
import pytest
import soundfile as sf

from winq.audio.dsp import GainRamp, db_to_gain, gain_to_db
from winq.audio.loader import AudioLoadError, compute_peaks, load_audio


def tone(seconds: float, sr: int, channels: int) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    mono = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    return np.stack([mono] * channels, axis=1)


def test_db_gain_roundtrip():
    assert db_to_gain(0.0) == 1.0
    assert db_to_gain(-math.inf) == 0.0
    assert db_to_gain(-200) == 0.0
    assert db_to_gain(-6.0) == pytest.approx(0.501, abs=1e-3)
    assert gain_to_db(0.0) == -math.inf
    assert gain_to_db(db_to_gain(-12.5)) == pytest.approx(-12.5)


def test_gain_ramp_reaches_target_exactly():
    r = GainRamp(1.0, 10)
    assert r.next_block(4) == 1.0
    r.set_target(0.0)
    g = r.next_block(6)
    assert g[-1] == pytest.approx(0.4)
    g = r.next_block(6)
    assert g[3] == 0.0 and r.current == 0.0
    assert r.next_block(3) == 0.0


def test_load_mono_wav_resampled_to_stereo(tmp_path):
    path = tmp_path / "mono.wav"
    sf.write(path, tone(1.0, 44100, 1), 44100)
    audio = load_audio(path, 48000)
    assert audio.data.shape[1] == 2
    assert abs(audio.frames - 48000) <= 2
    assert audio.data.dtype == np.float32 and audio.data.flags.c_contiguous
    assert audio.source_channels == 1 and audio.source_samplerate == 44100
    np.testing.assert_array_equal(audio.data[:, 0], audio.data[:, 1])
    assert audio.duration == pytest.approx(1.0, abs=1e-3)


def test_load_mp3(tmp_path):
    path = tmp_path / "tone.mp3"
    sf.write(path, tone(0.5, 48000, 2), 48000, format="MP3")
    audio = load_audio(path, 48000)
    assert audio.duration == pytest.approx(0.5, abs=0.06)  # MP3 adds encoder padding
    assert np.abs(audio.data).max() > 0.3


def test_load_rejects_unsupported_and_broken(tmp_path):
    with pytest.raises(AudioLoadError):
        load_audio(tmp_path / "song.m4a", 48000)
    bad = tmp_path / "broken.wav"
    bad.write_bytes(b"not audio")
    with pytest.raises(AudioLoadError):
        load_audio(bad, 48000)


def test_peaks_shape_and_values():
    data = np.zeros((1000, 2), dtype=np.float32)
    data[300] = 0.9
    data[700] = -0.4
    peaks = compute_peaks(data, bucket=256)
    assert peaks.shape == (4, 2)
    assert peaks[1, 1] == pytest.approx(0.9)
    assert peaks[2, 0] == pytest.approx(-0.4)
