"""One WinQ per Windows user. A second launch (e.g. double-clicking a .winq file while
WinQ is open) hands its show file to the running WinQ, which comes to the front and
opens it, and then exits. Uses a Qt local socket (a named pipe on Windows).
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import json
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

TIMEOUT_MS = 1500
ACK = b"ok\n"


def server_name() -> str:
    """Per user, so two people signed in to one PC each get their own WinQ."""
    try:
        user = getpass.getuser()
    except Exception:  # no user name available: still one WinQ per machine
        user = "default"
    return "WinQ-" + hashlib.sha1(user.encode("utf-8")).hexdigest()[:12]


def _allow_running_instance_to_come_forward() -> None:
    """Windows only lets the foreground process hand the foreground to another one."""
    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY


def send_to_running(show: str | None, name: str | None = None) -> bool:
    """If a WinQ is already running, ask it to come forward (and open `show`). Returns
    True if it answered, so this process should exit."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(TIMEOUT_MS):
        return False
    _allow_running_instance_to_come_forward()
    sock.write(json.dumps({"open": show}).encode("utf-8") + b"\n")
    sock.flush()
    sock.waitForBytesWritten(TIMEOUT_MS)
    # The reply may already have arrived while writing, so check before waiting.
    while sock.bytesAvailable() < len(ACK) and sock.waitForReadyRead(TIMEOUT_MS):
        pass
    ok = bytes(sock.readAll().data()).startswith(ACK)
    sock.disconnectFromServer()
    return ok


class InstanceServer(QObject):
    """Listens for later launches. `activate_requested(path)` fires with the show file
    they were given ("" for none)."""

    activate_requested = Signal(str)

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self.name = name or server_name()
        if not self._server.listen(self.name):
            QLocalServer.removeServer(self.name)  # left over after a crash
            self._server.listen(self.name)

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        while (sock := self._server.nextPendingConnection()) is not None:
            sock.setParent(self)
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock: QLocalSocket) -> None:
        if not sock.canReadLine():
            return
        line = bytes(sock.readLine().data())
        try:
            show = json.loads(line.decode("utf-8")).get("open") or ""
        except (ValueError, AttributeError):
            show = ""
        sock.write(ACK)
        sock.flush()
        self.activate_requested.emit(str(show))
