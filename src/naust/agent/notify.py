"""Minimal sd_notify(3): tell systemd what the agent knows, when it runs under it.

No dependency, no daemon: one datagram per message on ``NOTIFY_SOCKET``.
Outside systemd every call is a no-op that returns ``False``.
"""

import os
import socket


def send(message: str) -> bool:
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return False
    if path.startswith("@"):
        path = "\0" + path[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(path)
            sock.sendall(message.encode())
    except OSError:
        return False
    return True


def ready() -> bool:
    return send("READY=1")


def status(text: str) -> bool:
    return send(f"STATUS={text}")


def stopping() -> bool:
    return send("STOPPING=1")


def extend_timeout(seconds: float) -> bool:
    return send(f"EXTEND_TIMEOUT_USEC={int(seconds * 1_000_000)}")
