"""Shared fixtures: a capturing HTTP server and a short socket directory."""

import asyncio
import socket
import tempfile
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web


def _can_bind_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
    except OSError:
        return False
    return True


NETWORK = _can_bind_loopback()
requires_network = pytest.mark.skipif(
    not NETWORK, reason="binding loopback sockets is not permitted here (build sandbox)"
)


@dataclass
class Capture:
    """Everything POSTed to the server, plus a way to make it fail on purpose."""

    url: str
    requests: list[dict[str, Any]] = field(default_factory=list)
    fail_next: int = 0
    delay: float = 0.0

    def by_path(self, path: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"] == path]


@pytest.fixture
async def capture() -> AsyncIterator[Capture]:
    if not NETWORK:
        pytest.skip("binding loopback sockets is not permitted here (build sandbox)")
    state = Capture(url="")

    async def handle(request: web.Request) -> web.Response:
        if state.delay:
            await asyncio.sleep(state.delay)
        body = await request.json()
        state.requests.append(
            {"path": request.path, "headers": dict(request.headers), "json": body}
        )
        if state.fail_next > 0:
            state.fail_next -= 1
            return web.Response(status=500, text="try again")
        return web.Response(status=204)

    app = web.Application()
    app.router.add_post("/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = next(a[1] for a in runner.addresses if isinstance(a, tuple))
    state.url = f"http://127.0.0.1:{port}"
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """Unix socket paths are limited to about 100 bytes; pytest's tmp_path is longer."""

    with tempfile.TemporaryDirectory(prefix="naust-") as directory:
        yield Path(directory)
