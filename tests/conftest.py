"""Shared test helpers."""
from __future__ import annotations

import socket

from tsdiscord.config import BridgeConfig


def make_cfg(**overrides) -> BridgeConfig:
    """Build a BridgeConfig with sane defaults for tests."""
    base = dict(
        ts_host="127.0.0.1",
        ts_ssh_port=10022,
        ts_query_user="serveradmin",
        ts_query_password="secret",
        discord_webhook_url="http://127.0.0.1:1/webhook",
    )
    base.update(overrides)
    return BridgeConfig(**base)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
