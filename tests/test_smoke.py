"""Phase 0 smoke tests: the stack is installed correctly and the window opens."""

from __future__ import annotations

import numpy as np
import soundfile as sf
import soxr

from winq.app import load_theme
from winq.audio.devices import list_output_devices


def test_soundfile_supports_required_formats():
    formats = sf.available_formats()
    for fmt in ("WAV", "MP3", "FLAC", "OGG", "AIFF"):
        assert fmt in formats, f"libsndfile build lacks {fmt}"


def test_soxr_resamples():
    tone = np.sin(np.linspace(0, 2 * np.pi * 440, 44100, dtype=np.float32))
    out = soxr.resample(tone, 44100, 48000)
    assert abs(len(out) - 48000) <= 1


def test_list_output_devices_runs():
    devices = list_output_devices()
    assert isinstance(devices, list)
    assert all(d.max_output_channels > 0 for d in devices)


def test_theme_is_bundled():
    assert "QWidget" in load_theme()


def test_main_window_opens(make_window):
    window = make_window()
    window.show()
    assert window.windowTitle().startswith("WinQ")
