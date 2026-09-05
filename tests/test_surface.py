"""The local HTTP surface: reads on TCP, commands only on the socket."""

from pathlib import Path

import aiohttp
from conftest import requires_network

from naust.agent.metrics import AgentMetrics
from naust.agent.status import AgentStatus
from naust.agent.supervisor import BackendState, SaveFiles
from naust.agent.surface import Surface


@requires_network
async def test_surface_routes(tmp_path: Path, socket_dir: Path) -> None:
    status = AgentStatus(
        world="w",
        game="valheim",
        capabilities={"presence": "inferred"},
        max_players=10,
        save_files=SaveFiles((tmp_path / "w.db", tmp_path / "w.fwl")),
    )
    metrics = AgentMetrics()
    drains: list[str] = []
    surface = Surface(
        status,
        metrics,
        request_drain=drains.append,
        save_kind="signal",
        socket_path=socket_dir / "w.sock",
        tcp=("127.0.0.1", 0),
    )
    await surface.start()
    assert surface.tcp_port
    tcp = f"http://127.0.0.1:{surface.tcp_port}"

    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{tcp}/v1/status") as r:
                assert r.status == 200
                assert (await r.json())["world"] == "w"
            async with http.get(f"{tcp}/readyz") as r:
                assert r.status == 503
            status.set_condition("Ready", "True", "test")
            status.state = BackendState.READY
            metrics.update(status, last_save_unix=None, start_unix=None)
            async with http.get(f"{tcp}/readyz") as r:
                assert r.status == 200
            async with http.get(f"{tcp}/healthz") as r:
                assert r.status == 200
            async with http.get(f"{tcp}/metrics") as r:
                body = await r.text()
                assert 'naust_backend_state{state="READY",world="w"} 1.0' in body
                assert 'naust_backend_ready{world="w"} 1.0' in body
            async with http.post(f"{tcp}/v1/drain") as r:
                assert r.status in (404, 405), "commands are not accepted on the TCP listener"

        async with aiohttp.ClientSession(
            connector=aiohttp.UnixConnector(path=str(socket_dir / "w.sock"))
        ) as unix:
            async with unix.post("http://naust/v1/drain") as r:
                assert r.status == 202
            async with unix.post("http://naust/v1/save") as r:
                assert r.status == 501
            status.state = BackendState.FAILED
            async with unix.get("http://naust/healthz") as r:
                assert r.status == 503
    finally:
        await surface.stop()

    assert drains == ["command"]
    assert not (socket_dir / "w.sock").exists()
