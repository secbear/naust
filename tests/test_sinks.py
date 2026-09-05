"""Sinks deliver, retry, and never block."""

import asyncio

import pytest
from conftest import Capture

from naust.agent.sinks import DiscordSink, Dispatcher, WebhookSink, render_discord


def event(type_: str, **data) -> dict:
    return {"specversion": "1.0", "type": f"io.naust.{type_}", "naustsequence": 1, "data": data}


async def test_webhook_sink_posts_cloudevents_with_bearer(capture: Capture) -> None:
    dispatcher = Dispatcher([WebhookSink(f"{capture.url}/hook", token="t0k")])
    await dispatcher.start()

    dispatcher.publish(event("backend.ready", world="w"))
    await dispatcher.close()

    [request] = capture.by_path("/hook")
    assert request["headers"]["Content-Type"] == "application/cloudevents+json"
    assert request["headers"]["Authorization"] == "Bearer t0k"
    assert request["json"]["type"] == "io.naust.backend.ready"
    assert dispatcher.delivered["webhook"] == 1


async def test_discord_sink_renders_and_skips(capture: Capture) -> None:
    dispatcher = Dispatcher([DiscordSink(f"{capture.url}/discord")])
    await dispatcher.start()

    dispatcher.publish(event("backend.starting", world="w"))  # not rendered
    dispatcher.publish(event("backend.join", world="midgard", kind="code", code="604510"))
    await dispatcher.close()

    [request] = capture.by_path("/discord")
    assert request["json"]["content"] == "🔑 midgard join code: **604510**"
    assert request["json"]["allowed_mentions"] == {"parse": []}


async def test_dispatcher_retries_then_succeeds(capture: Capture) -> None:
    capture.fail_next = 2
    dispatcher = Dispatcher([WebhookSink(f"{capture.url}/hook")], attempts=3, backoff=0.01)
    await dispatcher.start()

    dispatcher.publish(event("x"))
    await dispatcher.close()

    assert len(capture.by_path("/hook")) == 3
    assert dispatcher.delivered["webhook"] == 1
    assert dispatcher.failed["webhook"] == 0


async def test_dispatcher_counts_failure_after_attempts(capture: Capture) -> None:
    capture.fail_next = 10
    dispatcher = Dispatcher([WebhookSink(f"{capture.url}/hook")], attempts=2, backoff=0.01)
    await dispatcher.start()

    dispatcher.publish(event("x"))
    await dispatcher.close()

    assert dispatcher.failed["webhook"] == 1
    assert dispatcher.delivered["webhook"] == 0


async def test_publish_never_blocks_and_drops_oldest(capture: Capture) -> None:
    capture.delay = 5.0
    dispatcher = Dispatcher([WebhookSink(f"{capture.url}/hook")], queue_size=2, timeout=0.2)
    await dispatcher.start()

    started = asyncio.get_running_loop().time()
    for i in range(6):
        dispatcher.publish(event("x", i=i))
    assert asyncio.get_running_loop().time() - started < 0.1
    assert dispatcher.dropped["webhook"] >= 3

    await asyncio.wait_for(dispatcher.close(flush_timeout=0.1), timeout=5)


async def test_unreachable_sink_is_a_counted_failure() -> None:
    dispatcher = Dispatcher(
        [WebhookSink("http://127.0.0.1:9/hook")], attempts=2, backoff=0.01, timeout=0.5
    )
    await dispatcher.start()
    dispatcher.publish(event("x"))
    await dispatcher.close()

    assert dispatcher.failed["webhook"] == 1


async def test_dispatcher_without_sinks_is_inert() -> None:
    dispatcher = Dispatcher([])
    await dispatcher.start()
    dispatcher.publish(event("x"))
    await dispatcher.close()


@pytest.mark.parametrize(
    ("ce", "expected"),
    [
        (event("backend.ready", world="w", version="1.0"), "🟢 w is up (version 1.0)."),
        (event("backend.ready", world="w", version=None), "🟢 w is up."),
        (
            event("backend.join", world="w", kind="address", address="1.2.3.4", port=2456),
            "🔑 w at 1.2.3.4:2456",
        ),
        (
            event("presence.changed", world="w", count=2, joined=["A"], left=[]),
            "👋 A joined w (2 online).",
        ),
        (
            event("presence.changed", world="w", count=0, joined=[], left=["A"]),
            "🚪 A left w (0 online).",
        ),
        (event("presence.changed", world="w", count=3, joined=[], left=[]), "👥 w: 3 online."),
        (
            event(
                "drain.finished",
                world="w",
                succeeded=True,
                session={"durationSeconds": 3600, "peakPlayers": 4},
            ),
            "🌙 w saved and stopped. Session 60 min, peak 4 players.",
        ),
        (
            event("drain.finished", world="w", succeeded=False, detail="save timed out"),
            "🔴 w did not stop cleanly: save timed out",
        ),
        (event("backend.failed", world="w", reason="exited 3"), "🔴 w needs attention: exited 3"),
        (event("save.completed", world="w"), None),
        (event("backend.version", world="w"), None),
    ],
)
def test_render_discord(ce: dict, expected: str | None) -> None:
    assert render_discord(ce) == expected
