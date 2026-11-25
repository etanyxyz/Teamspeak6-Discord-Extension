"""Tests for the Discord webhook worker against a local aiohttp mock."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

from conftest import free_port, make_cfg
from tsdiscord import parser
from tsdiscord.discord import DiscordWorker

EVENT = parser.TextMessage(
    target_mode=3, text="hello", sender_clid=7, sender_name="Alice",
    sender_uid="user1",
)


@pytest.fixture
async def webhook():
    calls: list[dict] = []

    async def ok(request):
        calls.append(await request.json())
        return web.Response(status=204)

    async def limited_header(request):
        calls.append(await request.json())
        if len(calls) == 1:
            return web.Response(status=429, headers={"Retry-After": "0.2"})
        return web.Response(status=204)

    async def limited_json(request):
        calls.append(await request.json())
        if len(calls) == 1:
            return web.json_response({"retry_after": 200}, status=429)
        return web.Response(status=204)

    async def flaky(request):
        calls.append(await request.json())
        if len(calls) == 1:
            return web.Response(status=500, text="boom")
        return web.Response(status=204)

    async def bad(request):
        calls.append(await request.json())
        return web.Response(status=404, text="not found here")

    app = web.Application()
    app.router.add_post("/ok", ok)
    app.router.add_post("/limited-header", limited_header)
    app.router.add_post("/limited-json", limited_json)
    app.router.add_post("/flaky", flaky)
    app.router.add_post("/bad", bad)

    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield SimpleNamespace(port=port, calls=calls)
    finally:
        await runner.cleanup()


@pytest_asyncio.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


def _worker(session, webhook, path: str) -> DiscordWorker:
    cfg = make_cfg(discord_webhook_url=f"http://127.0.0.1:{webhook.port}{path}")
    return DiscordWorker(session, cfg, asyncio.Queue())


def _payload() -> dict:
    from tsdiscord import formatter
    return formatter.build_payload(EVENT, channel_name="General")


async def test_send_success(session, webhook):
    worker = _worker(session, webhook, "/ok")
    assert await worker.send_payload(_payload()) is True
    assert len(webhook.calls) == 1
    assert webhook.calls[0]["allowed_mentions"] == {"parse": []}
    assert webhook.calls[0]["embeds"][0]["title"] == "Alice"


async def test_429_header_honoured(session, webhook):
    worker = _worker(session, webhook, "/limited-header")
    assert await worker.send_payload(_payload()) is True
    assert len(webhook.calls) == 2


async def test_429_json_body_honoured(session, webhook):
    worker = _worker(session, webhook, "/limited-json")
    assert await worker.send_payload(_payload()) is True
    assert len(webhook.calls) == 2


async def test_5xx_retried(session, webhook):
    worker = _worker(session, webhook, "/flaky")
    assert await worker.send_payload(_payload()) is True
    assert len(webhook.calls) == 2


async def test_404_drops_event(session, webhook):
    worker = _worker(session, webhook, "/bad")
    assert await worker.send_payload(_payload()) is False
    assert len(webhook.calls) == 1


async def test_run_consumes_queue(session, webhook):
    queue: asyncio.Queue = asyncio.Queue()
    cfg = make_cfg(discord_webhook_url=f"http://127.0.0.1:{webhook.port}/ok")
    worker = DiscordWorker(session, cfg, queue)
    task = asyncio.create_task(worker.run())
    queue.put_nowait(EVENT)
    queue.put_nowait(
        parser.ClientJoined(clid=2, uid="u2", nickname="Bob",
                            channel_id=1, client_type=0)
    )
    # wait until both events are delivered
    deadline = asyncio.get_event_loop().time() + 5
    while len(webhook.calls) < 2:
        if asyncio.get_event_loop().time() > deadline:
            task.cancel()
            raise AssertionError("events not delivered in time")
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    titles = [c["embeds"][0]["title"] for c in webhook.calls]
    assert titles == ["Alice", "User joined"]


async def test_run_survives_bad_event(session, webhook):
    queue: asyncio.Queue = asyncio.Queue()
    cfg = make_cfg(discord_webhook_url=f"http://127.0.0.1:{webhook.port}/ok")
    worker = DiscordWorker(session, cfg, queue)
    task = asyncio.create_task(worker.run())
    queue.put_nowait(object())  # unformattable event
    queue.put_nowait(EVENT)
    deadline = asyncio.get_event_loop().time() + 5
    while len(webhook.calls) < 1:
        if asyncio.get_event_loop().time() > deadline:
            task.cancel()
            raise AssertionError("valid event not delivered")
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert webhook.calls[0]["embeds"][0]["title"] == "Alice"


async def test_url_never_in_logs(session, webhook, caplog):
    import logging
    worker = _worker(session, webhook, "/bad")
    with caplog.at_level(logging.DEBUG, logger="tsdiscord.discord"):
        await worker.send_payload(_payload())
    assert f"127.0.0.1:{webhook.port}" not in caplog.text
