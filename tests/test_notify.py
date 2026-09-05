import socket
from pathlib import Path

import pytest

from naust.agent import notify


def test_no_socket_means_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert notify.ready() is False


def test_messages_reach_the_notify_socket(
    socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = socket_dir / "notify.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(path))
        server.settimeout(2)
        monkeypatch.setenv("NOTIFY_SOCKET", str(path))

        assert notify.ready()
        assert notify.status("ready, 2 players")
        assert notify.stopping()
        assert notify.extend_timeout(1.5)

        received = [server.recv(256).decode() for _ in range(4)]

    assert received == [
        "READY=1",
        "STATUS=ready, 2 players",
        "STOPPING=1",
        "EXTEND_TIMEOUT_USEC=1500000",
    ]


def test_unreachable_socket_is_false(socket_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", str(socket_dir / "missing.sock"))
    assert notify.ready() is False
