"""Entry point: wires config -> TS client -> queue -> Discord worker.

Handles SIGTERM/SIGINT and reconnects to the TS server with exponential
backoff (capped at 60 s) whenever the query stream dies.
"""
from __future__ import annotations

import asyncio
import logging
import signal

import aiohttp

from .config import BridgeConfig, ConfigError, load_config
from .discord import DiscordWorker
from .ts_client import TSClient

log = logging.getLogger("tsdiscord.main")

_RECONNECT_BASE = 1.0
_RECONNECT_CAP = 60.0


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


async def _main(cfg: BridgeConfig) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    async with aiohttp.ClientSession() as session:
        worker = DiscordWorker(session, cfg, queue)
        worker_task = asyncio.create_task(worker.run())
        client = TSClient(cfg, queue)

        backoff = _RECONNECT_BASE
        while not stop.is_set():
            try:
                await client.run()
            except ConfigError:
                raise
            except Exception as e:  # noqa: BLE001 - reconnect on any failure
                log.warning("TS connection lost: %s: %s", type(e).__name__, e)
            if stop.is_set():
                break
            delay = min(_RECONNECT_CAP, backoff)
            backoff = min(_RECONNECT_CAP, backoff * 2)
            log.info("reconnecting in %.0fs", delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    log.info("bridge stopped")


def main() -> None:
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}")  # no logging configured yet; keep on stderr
        raise SystemExit(2)
    setup_logging(cfg.log_level)
    log.info(
        "starting TS6->Discord bridge (ts=%s:%s, channel=%r, private_forward=%s)",
        cfg.ts_host, cfg.ts_ssh_port, cfg.ts_bridge_channel,
        cfg.forward_private_messages,
    )
    try:
        asyncio.run(_main(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
