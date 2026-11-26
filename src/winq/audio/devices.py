"""Output-device discovery (thin wrapper around sounddevice)."""

from __future__ import annotations

from dataclasses import dataclass

import sounddevice as sd


@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str
    host_api: str
    max_output_channels: int
    default_samplerate: float


def list_output_devices() -> list[OutputDevice]:
    """All devices that can output audio, across every host API (WASAPI, MME, ...)."""
    host_apis = sd.query_hostapis()
    devices: list[OutputDevice] = []
    for index, info in enumerate(sd.query_devices()):
        if info["max_output_channels"] <= 0:
            continue
        devices.append(
            OutputDevice(
                index=index,
                name=info["name"],
                host_api=host_apis[info["hostapi"]]["name"],
                max_output_channels=info["max_output_channels"],
                default_samplerate=info["default_samplerate"],
            )
        )
    return devices


WASAPI = "Windows WASAPI"


def preferred_output_device_index() -> int | None:
    """Windows' default output, opened through WASAPI when available (lower latency than
    PortAudio's MME default). Falls back to PortAudio's default device."""
    for api in sd.query_hostapis():
        if api["name"] == WASAPI and api.get("default_output_device", -1) >= 0:
            return api["default_output_device"]
    try:
        index = sd.default.device[1]
    except Exception:
        return None
    return None if index is None or index < 0 else index


def default_output_device() -> OutputDevice | None:
    """The device WinQ uses when none is chosen in Preferences, or None if there isn't one."""
    index = preferred_output_device_index()
    if index is None:
        return None
    return next((d for d in list_output_devices() if d.index == index), None)
