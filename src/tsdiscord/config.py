"""Configuration for the TS6 -> Discord bridge.

Values come from the environment (optionally preloaded from a .env file).
Secrets stay in .env only and are never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import dotenv


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_VALID_BOOLS = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BridgeConfig:
    # TeamSpeak 6 (SSH ServerQuery interface)
    ts_host: str
    ts_ssh_port: int
    ts_query_user: str
    ts_query_password: str
    # Discord webhook
    discord_webhook_url: str
    # Optional WebQuery API key (kept for manual/auxiliary use).
    ts_api_key: str = ""
    # Nickname the bridge's query client uses on the TS server.
    bridge_nickname: str = "TS6-Discord-Bridge"
    # Channel (by name) the bridge's query client sits in; channel chat is
    # only visible from that channel (see docs/sample-events.txt). None means
    # the server's default channel.
    ts_bridge_channel: str | None = None
    # Behaviour
    forward_private_messages: bool = False
    ignore_client_uids: frozenset[str] = frozenset()
    log_level: str = "INFO"


def _parse_bool(raw: str, name: str) -> bool:
    v = raw.strip().lower()
    if v in _VALID_BOOLS:
        return True
    if v in ("", "0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _split_uids(raw: str) -> frozenset[str]:
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def _port(raw: str, name: str) -> int:
    try:
        p = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer port, got {raw!r}")
    if not 1 <= p <= 65535:
        raise ConfigError(f"{name} must be in 1..65535, got {p}")
    return p


def load_config(env_file: str | None = None) -> BridgeConfig:
    """Load configuration from the environment.

    If *env_file* is given it is loaded into the environment first
    (existing variables win, so container env vars can override .env).
    """
    # Load the .env file explicitly (no directory walk): in containers the
    # env vars are set directly and a missing .env is a no-op. Existing
    # environment variables always win.
    dotenv.load_dotenv(env_file or ".env", override=False)

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise ConfigError(
            "DISCORD_WEBHOOK_URL is required (set it in .env)"
        )

    user = os.environ.get("TS_QUERY_USER", "").strip()
    password = os.environ.get("TS_QUERY_PASSWORD", "").strip()
    if not user or not password:
        raise ConfigError("TS_QUERY_USER and TS_QUERY_PASSWORD are required")

    level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    if level not in _VALID_LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {sorted(_VALID_LOG_LEVELS)}")

    return BridgeConfig(
        ts_host=os.environ.get("TS_HOST", "ts6").strip(),
        ts_ssh_port=_port(os.environ.get("TS_SSH_PORT", "10022"), "TS_SSH_PORT"),
        ts_query_user=user,
        ts_query_password=password,
        ts_api_key=os.environ.get("TS_QUERY_API_KEY", "").strip(),
        bridge_nickname=os.environ.get("BRIDGE_NICKNAME", "TS6-Discord-Bridge").strip(),
        ts_bridge_channel=(os.environ.get("TS_BRIDGE_CHANNEL", "").strip() or None),
        discord_webhook_url=webhook,
        forward_private_messages=_parse_bool(
            os.environ.get("FORWARD_PRIVATE_MESSAGES", "false"),
            "FORWARD_PRIVATE_MESSAGES",
        ),
        ignore_client_uids=_split_uids(os.environ.get("IGNORE_CLIENT_UIDS", "")),
        log_level=level,
    )
