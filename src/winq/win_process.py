"""Is another WinQ process still running? (Used so autosave recovery never takes the
backup of a WinQ window that is still open.) Windows only; elsewhere always False."""

from __future__ import annotations

import contextlib
import os
import sys

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
# Executables a WinQ session can run as: the built app, or Python when run from source.
WINQ_EXECUTABLES = frozenset({"winq.exe", "python.exe", "pythonw.exe"})


def winq_process_alive(pid: int) -> bool:
    """True if `pid` is a running process that looks like WinQ (not this one)."""
    if sys.platform != "win32" or pid <= 0 or pid == os.getpid():
        return False
    import ctypes
    from ctypes import wintypes

    with contextlib.suppress(AttributeError, OSError):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            if code.value != STILL_ACTIVE:
                return False
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return True  # alive, but we can't tell what it is: be safe, keep its backup
            return os.path.basename(buf.value).lower() in WINQ_EXECUTABLES
        finally:
            kernel32.CloseHandle(handle)
    return False


def pid_from_autosave_name(stem: str, prefix: str) -> int | None:
    """`session-1234` → 1234 (None if the name doesn't carry a pid)."""
    if not stem.startswith(prefix):
        return None
    tail = stem[len(prefix) :]
    return int(tail) if tail.isdigit() else None
