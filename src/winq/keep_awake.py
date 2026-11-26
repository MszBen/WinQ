"""Stop Windows from sleeping or turning the screen off (used while in Show mode).

Uses SetThreadExecutionState, which must be called from the thread that set it (the
GUI thread). Does nothing on other platforms.
"""

from __future__ import annotations

import contextlib
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def set_keep_awake(on: bool) -> bool:
    """Returns True if Windows accepted the request."""
    if sys.platform != "win32":
        return False
    import ctypes

    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    with contextlib.suppress(AttributeError, OSError):
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    return False
