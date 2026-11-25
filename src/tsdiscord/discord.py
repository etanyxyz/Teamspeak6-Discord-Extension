"""Discord webhook worker.

Consumes events from the queue, formats them (formatter) and POSTs them to
the webhook. Never crashes on delivery problems:
- HTTP 429: honour ``retry_after`` (header or JSON body) and retry;
- HTTP 5xx / network errors: retry with capped exponential backoff;
- anything else: log (without the URL) and drop the event.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from . import formatter
from .config import BridgeConfig

log = logging.getLogger("tsdiscord.discord")

_RETRY_BASE = 2.0
_RETRY_CAP = 60.0


async def _retry_after_seconds(resp: aiohttp.ClientResponse) -> float:
    """Discord webhooks send Retry-After in seconds (header) or ms (JSON)."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    try:
        data = await resp.json()
        if isinstance(data, dict) and data.get("retry_after") is not None:
            return max(0.0, float(data["retry_after"]) / 1000.0)
    except Exception:  # noqa: BLE001
        pass
    return _RETRY_BASE


class DiscordWorker:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        cfg: BridgeConfig,
        queue: asyncio.Queue,
    ) -> None:
        self._session = session
        self._url = cfg.discord_webhook_url
        self._queue = queue
        self._channel_name = cfg.ts_bridge_channel or "default channel"

    async def send_payload(self, payload: dict) -> bool:
        """POST one payload. True on success; False when the event is dropped."""
        attempt = 0
        while True:
            try:
                async with self._session.post(
                    self._url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 204:
                        return True
                    if resp.status == 429:
                        wait = await _retry_after_seconds(resp)
                        log.warning("webhook rate-limited (429); waiting %.1fs", wait)
                        await asyncio.sleep(wait)
                        continue
                    if 500 <= resp.status < 600:
                        delay = min(_RETRY_CAP, _RETRY_BASE * (2 ** attempt))
                        attempt += 1
                        log.warning("webhook %s; retrying in %.1fs", resp.status, delay)
                        await asyncio.sleep(delay)
                        continue
                    body = (await resp.text())[:500]
                    log.error(
                        "webhook rejected (%s) — event dropped: %s", resp.status, body
                    )
                    return False
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                delay = min(_RETRY_CAP, _RETRY_BASE * (2 ** attempt))
                attempt += 1
                log.warning(
                    "webhook unreachable (%s); retrying in %.1fs", type(e).__name__, delay
                )
                await asyncio.sleep(delay)

    async def run(self) -> None:
        """Read events until cancelled."""
        while True:
            event = await self._queue.get()
            try:
                payload = formatter.build_payload(event, channel_name=self._channel_name)
            except Exception:  # noqa: BLE001 - never crash the worker
                log.exception("failed to format event; dropping it")
                continue
            try:
                await self.send_payload(payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("unexpected webhook failure; event dropped")
