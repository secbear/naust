"""Event sinks: where CloudEvents go, and the rule that they never block a drain.

Each sink has its own bounded queue and worker. Delivery is retried with
backoff; after the attempts run out the event is counted as failed and the
worker moves on. When the queue is full the oldest event is dropped, because
the newest one is the one that says what is happening now.
"""

import asyncio
import contextlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

CLOUDEVENTS_JSON = "application/cloudevents+json"


class Sink(Protocol):
    name: str

    async def deliver(self, session: aiohttp.ClientSession, cloudevent: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class WebhookSink:
    """POST the CloudEvent as-is. The bearer token identifies this agent."""

    url: str
    token: str | None = None
    name: str = "webhook"

    async def deliver(self, session: aiohttp.ClientSession, cloudevent: dict[str, Any]) -> None:
        headers = {"Content-Type": CLOUDEVENTS_JSON}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with session.post(self.url, json=cloudevent, headers=headers) as response:
            response.raise_for_status()


def render_discord(cloudevent: dict[str, Any]) -> str | None:
    """A short human line per event people care about; ``None`` means skip."""

    data = cloudevent.get("data", {})
    world = data.get("world", "world")
    match cloudevent.get("type", "").removeprefix("io.naust."):
        case "backend.ready":
            version = data.get("version")
            suffix = f" (version {version})" if version else ""
            return f"🟢 {world} is up{suffix}."
        case "backend.join":
            if data.get("kind") == "code":
                return f"🔑 {world} join code: **{data.get('code')}**"
            return f"🔑 {world} at {data.get('address')}:{data.get('port')}"
        case "presence.changed":
            count = data.get("count", 0)
            if data.get("joined"):
                who = ", ".join(data["joined"])
                return f"👋 {who} joined {world} ({count} online)."
            if data.get("left"):
                who = ", ".join(data["left"])
                return f"🚪 {who} left {world} ({count} online)."
            return f"👥 {world}: {count} online."
        case "drain.finished":
            summary = data.get("session", {})
            minutes = round(summary.get("durationSeconds", 0) / 60)
            peak = summary.get("peakPlayers", 0)
            if data.get("succeeded"):
                return f"🌙 {world} saved and stopped. Session {minutes} min, peak {peak} players."
            return f"🔴 {world} did not stop cleanly: {data.get('detail')}"
        case "backend.failed" | "backend.exited" | "backend.refused":
            return f"🔴 {world} needs attention: {data.get('reason') or data.get('detail')}"
        case _:
            return None


@dataclass(frozen=True, slots=True)
class DiscordSink:
    url: str
    name: str = "discord"

    async def deliver(self, session: aiohttp.ClientSession, cloudevent: dict[str, Any]) -> None:
        content = render_discord(cloudevent)
        if content is None:
            return
        payload = {"content": content, "allowed_mentions": {"parse": []}}
        async with session.post(self.url, json=payload) as response:
            response.raise_for_status()


class Dispatcher:
    def __init__(
        self,
        sinks: Iterable[Sink],
        *,
        queue_size: int = 256,
        attempts: int = 3,
        backoff: float = 0.5,
        timeout: float = 10.0,
    ) -> None:
        self.sinks = list(sinks)
        self.queue_size = queue_size
        self.attempts = attempts
        self.backoff = backoff
        self.timeout = timeout
        self.delivered: Counter[str] = Counter()
        self.failed: Counter[str] = Counter()
        self.dropped: Counter[str] = Counter()
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if not self.sinks:
            return
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        for sink in self.sinks:
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
            self._queues[sink.name] = queue
            self._workers.append(
                asyncio.create_task(self._work(sink, queue), name=f"naust-sink-{sink.name}")
            )

    def publish(self, cloudevent: dict[str, Any]) -> None:
        for name, queue in self._queues.items():
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                    queue.task_done()
                self.dropped[name] += 1
            queue.put_nowait(cloudevent)

    async def close(self, flush_timeout: float = 10.0) -> None:
        """Give queued events a bounded chance to leave, then stop."""

        if self._queues:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*(q.join() for q in self._queues.values())), flush_timeout
                )
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        if self._session is not None:
            await self._session.close()

    async def _work(self, sink: Sink, queue: asyncio.Queue[dict[str, Any]]) -> None:
        assert self._session is not None
        while True:
            cloudevent = await queue.get()
            try:
                await self._deliver_with_retries(sink, cloudevent)
            finally:
                queue.task_done()

    async def _deliver_with_retries(self, sink: Sink, cloudevent: dict[str, Any]) -> None:
        assert self._session is not None
        for attempt in range(1, self.attempts + 1):
            try:
                await sink.deliver(self._session, cloudevent)
            except (aiohttp.ClientError, TimeoutError, OSError):
                if attempt == self.attempts:
                    self.failed[sink.name] += 1
                    return
                await asyncio.sleep(self.backoff * 2 ** (attempt - 1))
            else:
                self.delivered[sink.name] += 1
                return
