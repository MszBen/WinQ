"""Error log for the field: unexpected exceptions are written to
`%APPDATA%/WinQ/WinQ/logs/winq.log` instead of vanishing (the built app has no console),
and WinQ keeps running. Hard crashes (native faults) are dumped by faulthandler to
`crash.log` in the same folder.

Help → Open Log Folder shows the folder, so an operator can send the files with a report.
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import os
import sys
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

log = logging.getLogger("winq")
_fault_file = None  # kept open for faulthandler for the life of the process
_listeners: list[Callable[[str], None]] = []


def log_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) / "WinQ" / "WinQ" if base else Path.home() / ".winq"
    return root / "logs"


def install(version: str) -> Path | None:
    """Start logging to the log folder and catch every unhandled exception. Returns the
    log folder, or None if it can't be written (then nothing is logged)."""
    global _fault_file
    folder = log_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            folder / "winq.log", maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("WinQ %s started (Python %s)", version, sys.version.split()[0])
    with contextlib.suppress(OSError, RuntimeError):
        _fault_file = open(folder / "crash.log", "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(_fault_file)
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    return folder


def on_error(listener: Callable[[str], None]) -> None:
    """Call `listener(summary)` (on the thread that failed) after an error is logged."""
    _listeners.append(listener)


def _report(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    where: str,
) -> None:
    log.error("Unhandled exception %s", where, exc_info=(exc_type, exc, tb))
    summary = f"{exc_type.__name__}: {exc}"
    for listener in list(_listeners):
        with contextlib.suppress(Exception):
            listener(summary)


def _excepthook(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    _report(exc_type, exc, tb, "in the GUI")


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is None:
        return
    name = args.thread.name if args.thread else "?"
    _report(args.exc_type, args.exc_value, args.exc_traceback, f"in thread {name}")
