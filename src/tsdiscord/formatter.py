"""Pure formatting of TS events into Discord webhook payloads.

Safety rules (per spec):
- ``allowed_mentions: {"parse": []}`` on every payload;
- ``@everyone``/``@here``/role-id mentions neutralized with a zero-width space;
- markdown special characters escaped in user-controlled strings;
- Discord limits honoured: content 2000, embed description 4096,
  title 256, max 25 fields.
"""
from __future__ import annotations

import datetime
import re

from . import parser

ZWSP = "\u200b"
# Note: Discord rejects webhook usernames containing "discord" (case-
# insensitive), so this must not mention Discord.
BRIDGE_USERNAME = "TS6 \u2192 Bridge"

_LIMIT_CONTENT = 2000
_LIMIT_DESC = 4096
_LIMIT_TITLE = 256
_LIMIT_FIELDS = 25
_LIMIT_FIELD_VALUE = 1024
_LIMIT_EMBED_TOTAL = 6000

_MD_CHARS = r"_*~|`"
_MENTION_RE = re.compile(r"@(?=(everyone|here\b|\d{17,19}))")
_MD_RE = re.compile(rf"([{_MD_CHARS}])")


def neutralize_mentions(text: str) -> str:
    """Insert a zero-width space after @-mentions so Discord never parses them."""
    return _MENTION_RE.sub(lambda m: "@" + ZWSP, text)


def escape_markdown(text: str) -> str:
    """Escape Discord markdown in user-controlled text."""
    return _MD_RE.sub(r"\\\1", text)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def safe_text(text: str, limit: int) -> str:
    """Neutralize mentions, escape markdown, enforce the length limit."""
    return truncate(escape_markdown(neutralize_mentions(text)), limit)


def _now_rfc3339() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload(title: str, description: str, fields: list[tuple[str, str]] | None = None) -> dict:
    """Assemble the payload. All strings must already be safe (escaped,
    mention-neutralized) — length limits are enforced here as a backstop,
    including the 6000-char total embed budget."""
    title = truncate(title, _LIMIT_TITLE)
    field_rows = [
        (truncate(name, _LIMIT_TITLE),
         truncate(value or "\u2014", _LIMIT_FIELD_VALUE))
        for name, value in (fields or [])[:_LIMIT_FIELDS]
    ]
    budget = _LIMIT_EMBED_TOTAL - len(title) - sum(
        len(n) + len(v) for n, v in field_rows
    )
    embed: dict = {
        "title": title,
        "description": truncate(description, min(_LIMIT_DESC, max(budget, 1))),
        "timestamp": _now_rfc3339(),
    }
    if field_rows:
        embed["fields"] = [
            {"name": n, "value": v, "inline": True} for n, v in field_rows
        ]
    return {"username": BRIDGE_USERNAME, "allowed_mentions": {"parse": []}, "embeds": [embed]}


def build_payload(event: object, *, channel_name: str = "") -> dict:
    """Convert a parser dataclass into a Discord webhook payload."""
    if isinstance(event, parser.ClientJoined):
        return _payload(
            "User joined",
            f"**{safe_text(event.nickname, _LIMIT_DESC)}** "
            f"`{event.uid}`",
            fields=[("Joined channel", f"#{safe_text(channel_name, _LIMIT_DESC)}")],
        )
    if isinstance(event, parser.ClientLeft):
        name = event.nickname or "unknown"
        return _payload(
            "User left",
            f"**{safe_text(name, _LIMIT_DESC)}** `{event.uid}`",
            fields=[("Reason", safe_text(event.reason or "left", _LIMIT_DESC))],
        )
    if isinstance(event, parser.TextMessage):
        if event.target_mode == 3:
            target = "server"
        elif event.target_mode == 2:
            target = f"channel #{safe_text(channel_name, _LIMIT_DESC)}"
        else:
            target = "private (to the bridge client)"
        return _payload(
            safe_text(event.sender_name or "unknown", _LIMIT_TITLE),
            safe_text(event.text, _LIMIT_DESC),
            fields=[("Target", target)],
        )
    raise TypeError(f"unknown event type: {type(event)!r}")
