"""One WinQ per user: a second launch hands its show to the running WinQ and exits."""

from __future__ import annotations

import subprocess
import sys
import uuid

from winq.model.show_file import save_show
from winq.ui.main_window import legal_dir
from winq.ui.single_instance import InstanceServer, send_to_running

CLIENT = """
import sys
from PySide6.QtCore import QCoreApplication
from winq.ui.single_instance import send_to_running
app = QCoreApplication([])
sys.exit(0 if send_to_running(sys.argv[2] or None, sys.argv[1]) else 1)
"""


def test_no_running_instance_means_start_normally(qapp):
    assert not send_to_running(None, f"WinQ-test-{uuid.uuid4().hex}")


def test_second_launch_hands_its_show_to_the_running_one(qtbot):
    name = f"WinQ-test-{uuid.uuid4().hex}"
    server = InstanceServer(name)
    assert server.listening
    received: list[str] = []
    server.activate_requested.connect(received.append)
    client = subprocess.Popen([sys.executable, "-c", CLIENT, name, r"C:\shows\play.winq"])
    try:
        qtbot.waitUntil(lambda: bool(received), timeout=10000)
        qtbot.waitUntil(lambda: client.poll() is not None, timeout=10000)
    finally:
        if client.poll() is None:
            client.kill()
        server.close()
    assert received == [r"C:\shows\play.winq"]
    assert client.returncode == 0  # it was answered, so it exits instead of opening


def test_activation_opens_the_show_and_ignores_the_one_already_open(make_window, tmp_path):
    w = make_window()
    show = tmp_path / "other.winq"
    save_show(show, [], master_db=-3.0)
    w.activate_from_other_launch(str(show))
    assert w.show_path == show
    assert w.master_db == -3.0
    w.set_master_db(0.0)
    w.activate_from_other_launch(str(show))  # already open: not reloaded
    assert w.master_db == 0.0
    w.activate_from_other_launch("")  # plain second launch: just comes forward
    assert w.show_path == show


def test_licence_files_are_found():
    folder = legal_dir()
    assert (folder / "LICENSE.txt").is_file()
    assert (folder / "THIRD_PARTY_NOTICES.txt").is_file()
    assert (folder / "licenses" / "LGPL-3.0.txt").is_file()
