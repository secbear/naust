"""The agent's local HTTP surface: status, probes, commands, metrics.

Two listeners with different trust: a unix socket carries everything
including commands; an optional localhost TCP port carries only reads, so
metrics scrapers and container probes can reach it without being able to
drain the world.
"""

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from naust.agent.metrics import CONTENT_TYPE, AgentMetrics
from naust.agent.status import AgentStatus
from naust.agent.supervisor import BackendState


class Surface:
    def __init__(
        self,
        status: AgentStatus,
        metrics: AgentMetrics,
        *,
        request_drain: Callable[[str], None],
        save_kind: str,
        socket_path: Path | None = None,
        tcp: tuple[str, int] | None = None,
    ) -> None:
        self._status = status
        self._metrics = metrics
        self._request_drain = request_drain
        self._save_kind = save_kind
        self._socket_path = socket_path
        self._tcp = tcp
        self._runners: list[web.AppRunner] = []
        self.tcp_port: int | None = None

    async def start(self) -> None:
        if self._socket_path is not None:
            self._socket_path.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(FileNotFoundError):
                self._socket_path.unlink()
            runner = web.AppRunner(self._app(commands=True))
            await runner.setup()
            await web.UnixSite(runner, str(self._socket_path)).start()
            self._runners.append(runner)
        if self._tcp is not None:
            host, port = self._tcp
            runner = web.AppRunner(self._app(commands=False))
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()
            self._runners.append(runner)
            for address in runner.addresses:
                if isinstance(address, tuple):
                    self.tcp_port = int(address[1])

    async def stop(self) -> None:
        for runner in self._runners:
            await runner.cleanup()
        self._runners.clear()
        if self._socket_path is not None:
            with contextlib.suppress(FileNotFoundError):
                self._socket_path.unlink()

    def _app(self, *, commands: bool) -> web.Application:
        app = web.Application()
        app.router.add_get("/v1/status", self._get_status)
        app.router.add_get("/readyz", self._readyz)
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/metrics", self._get_metrics)
        if commands:
            app.router.add_post("/v1/drain", self._post_drain)
            app.router.add_post("/v1/save", self._post_save)
        return app

    async def _get_status(self, _request: web.Request) -> web.Response:
        return _json(self._status.document())

    async def _readyz(self, _request: web.Request) -> web.Response:
        ready = self._status.conditions["Ready"].status == "True"
        return _json({"ready": ready, "state": self._status.state.value}, 200 if ready else 503)

    async def _healthz(self, _request: web.Request) -> web.Response:
        healthy = self._status.state is not BackendState.FAILED
        return _json(
            {"healthy": healthy, "state": self._status.state.value}, 200 if healthy else 503
        )

    async def _get_metrics(self, _request: web.Request) -> web.Response:
        return web.Response(body=self._metrics.render(), headers={"Content-Type": CONTENT_TYPE})

    async def _post_drain(self, _request: web.Request) -> web.Response:
        self._request_drain("command")
        return _json({"accepted": True, "state": self._status.state.value}, 202)

    async def _post_save(self, _request: web.Request) -> web.Response:
        if self._save_kind != "command":
            return _json({"error": f"this game saves by {self._save_kind}; use drain"}, 501)
        return _json({"error": "save command not implemented for this game"}, 501)


def _json(payload: dict[str, Any], status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(payload), status=status, content_type="application/json")
