"""Tests for configuration loading."""
from __future__ import annotations

import pytest

from tsdiscord.config import BridgeConfig, ConfigError, load_config


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """chdir to an empty dir so the repo's .env can't leak into tests."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "DISCORD_WEBHOOK_URL", "TS_QUERY_USER", "TS_QUERY_PASSWORD",
        "TS_HOST", "TS_SSH_PORT", "TS_QUERY_API_KEY", "BRIDGE_NICKNAME",
        "TS_BRIDGE_CHANNEL", "FORWARD_PRIVATE_MESSAGES", "IGNORE_CLIENT_UIDS",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _set(iso, **kv):
    for k, v in kv.items():
        iso.setenv(k, v)


def test_missing_webhook_raises(isolated_env):
    _set(isolated_env, TS_QUERY_USER="u", TS_QUERY_PASSWORD="p")
    with pytest.raises(ConfigError, match="DISCORD_WEBHOOK_URL"):
        load_config()


def test_missing_credentials_raise(isolated_env):
    _set(isolated_env, DISCORD_WEBHOOK_URL="http://x")
    with pytest.raises(ConfigError, match="TS_QUERY_USER"):
        load_config()


def test_full_config(isolated_env):
    _set(
        isolated_env,
        DISCORD_WEBHOOK_URL="http://x",
        TS_QUERY_USER="serveradmin",
        TS_QUERY_PASSWORD="p",
        TS_HOST="ts6",
        TS_SSH_PORT="10022",
        BRIDGE_NICKNAME="My Bridge",
        TS_BRIDGE_CHANNEL="General",
        FORWARD_PRIVATE_MESSAGES="true",
        IGNORE_CLIENT_UIDS="u1, u2,,u3",
        LOG_LEVEL="debug",
    )
    cfg = load_config()
    assert isinstance(cfg, BridgeConfig)
    assert cfg.ts_host == "ts6"
    assert cfg.ts_ssh_port == 10022
    assert cfg.bridge_nickname == "My Bridge"
    assert cfg.ts_bridge_channel == "General"
    assert cfg.forward_private_messages is True
    assert cfg.ignore_client_uids == frozenset({"u1", "u2", "u3"})
    assert cfg.log_level == "DEBUG"


def test_defaults(isolated_env):
    _set(
        isolated_env,
        DISCORD_WEBHOOK_URL="http://x",
        TS_QUERY_USER="u",
        TS_QUERY_PASSWORD="p",
    )
    cfg = load_config()
    assert cfg.ts_ssh_port == 10022
    assert cfg.bridge_nickname == "TS6-Discord-Bridge"
    assert cfg.ts_bridge_channel is None
    assert cfg.forward_private_messages is False
    assert cfg.ignore_client_uids == frozenset()
    assert cfg.log_level == "INFO"


def test_bad_port_raises(isolated_env):
    _set(
        isolated_env,
        DISCORD_WEBHOOK_URL="http://x", TS_QUERY_USER="u", TS_QUERY_PASSWORD="p",
        TS_SSH_PORT="99999",
    )
    with pytest.raises(ConfigError, match="TS_SSH_PORT"):
        load_config()


def test_non_numeric_port_raises(isolated_env):
    _set(
        isolated_env,
        DISCORD_WEBHOOK_URL="http://x", TS_QUERY_USER="u", TS_QUERY_PASSWORD="p",
        TS_SSH_PORT="abc",
    )
    with pytest.raises(ConfigError, match="TS_SSH_PORT"):
        load_config()


def test_bad_bool_raises(isolated_env):
    _set(
        isolated_env,
        DISCORD_WEBHOOK_URL="http://x", TS_QUERY_USER="u", TS_QUERY_PASSWORD="p",
        FORWARD_PRIVATE_MESSAGES="maybe",
    )
    with pytest.raises(ConfigError, match="FORWARD_PRIVATE_MESSAGES"):
        load_config()


def test_bad_log_level_raises(isolated_env):
    _set(
        isolated_env,
        DISCORD_WEBHOOK_URL="http://x", TS_QUERY_USER="u", TS_QUERY_PASSWORD="p",
        LOG_LEVEL="LOUD",
    )
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_config()


def test_bool_false_variants(isolated_env):
    for v in ("0", "false", "no", "off", ""):
        _set(
            isolated_env,
            DISCORD_WEBHOOK_URL="http://x", TS_QUERY_USER="u", TS_QUERY_PASSWORD="p",
            FORWARD_PRIVATE_MESSAGES=v,
        )
        assert load_config().forward_private_messages is False
