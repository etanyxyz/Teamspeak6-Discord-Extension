"""Tests for the pure TS -> Discord formatter."""
from __future__ import annotations

import pytest

from tsdiscord import formatter, parser

ZWSP = "\u200b"


def test_joined_payload_shape():
    ev = parser.ClientJoined(
        clid=7, uid="user1", nickname="Alice Dev", channel_id=1, client_type=0
    )
    p = formatter.build_payload(ev, channel_name="General")
    assert p["username"] == "TS6 \u2192 Bridge"
    assert p["allowed_mentions"] == {"parse": []}
    emb = p["embeds"][0]
    assert emb["title"] == "User joined"
    assert "**Alice Dev**" in emb["description"]
    assert "`user1`" in emb["description"]
    assert emb["timestamp"].endswith("Z")
    assert emb["fields"] == [
        {"name": "Joined channel", "value": "#General", "inline": True}
    ]


def test_left_payload_unknown_name():
    ev = parser.ClientLeft(
        clid=10, nickname="", uid="", channel_id=1, reason_id=8, reason="disconnecting"
    )
    emb = formatter.build_payload(ev)["embeds"][0]
    assert emb["title"] == "User left"
    assert "**unknown**" in emb["description"]
    assert emb["fields"][0]["value"] == "disconnecting"


def test_message_payload_server_target():
    ev = parser.TextMessage(
        target_mode=3, text="hello", sender_clid=7, sender_name="Alice",
        sender_uid="user1",
    )
    p = formatter.build_payload(ev, channel_name="General")
    emb = p["embeds"][0]
    assert emb["title"] == "Alice"
    assert emb["description"] == "hello"
    assert emb["fields"][0]["value"] == "server"


def test_message_payload_channel_target():
    ev = parser.TextMessage(
        target_mode=2, text="hi", sender_clid=7, sender_name="Alice",
        sender_uid="user1",
    )
    emb = formatter.build_payload(ev, channel_name="My Ch")["embeds"][0]
    assert emb["fields"][0]["value"] == "channel #My Ch"


def test_message_payload_private_target():
    ev = parser.TextMessage(
        target_mode=1, text="psst", sender_clid=7, sender_name="Alice",
        sender_uid="user1",
    )
    emb = formatter.build_payload(ev)["embeds"][0]
    assert "private" in emb["fields"][0]["value"]


def test_unknown_event_raises():
    with pytest.raises(TypeError):
        formatter.build_payload("not an event")


# --- mention neutralisation ---------------------------------------------------

def test_neutralize_everyone_and_here():
    out = formatter.neutralize_mentions("hey @everyone and @here!")
    assert out == f"hey @{ZWSP}everyone and @{ZWSP}here!"


def test_neutralize_role_id():
    out = formatter.neutralize_mentions("ping @123456789012345678 now")
    assert out == f"ping @{ZWSP}123456789012345678 now"


def test_plain_at_mention_untouched():
    assert formatter.neutralize_mentions("mail bob@ts6.example") \
        == "mail bob@ts6.example"


def test_message_mention_neutralized_end_to_end():
    ev = parser.TextMessage(
        target_mode=3, text="go @everyone", sender_clid=1, sender_name="x",
        sender_uid="u",
    )
    p = formatter.build_payload(ev)
    emb = p["embeds"][0]
    assert emb["description"] == f"go @{ZWSP}everyone"
    assert p["allowed_mentions"] == {"parse": []}


# --- markdown escaping --------------------------------------------------------

def test_escape_markdown_chars():
    assert formatter.escape_markdown("a *b* _c_ `d` ~e~ |f|") \
        == "a \\*b\\* \\_c\\_ \\`d\\` \\~e\\~ \\|f\\|"


def test_nickname_markdown_escaped_in_payload():
    ev = parser.TextMessage(
        target_mode=3, text="hi", sender_clid=1, sender_name="*Bold* Name",
        sender_uid="u",
    )
    emb = formatter.build_payload(ev)["embeds"][0]
    assert emb["title"] == "\\*Bold\\* Name"


# --- truncation ---------------------------------------------------------------

def test_truncate_short_unchanged():
    assert formatter.truncate("abc", 10) == "abc"


def test_truncate_long_gets_ellipsis():
    out = formatter.truncate("x" * 5000, 4096)
    assert len(out) == 4096
    assert out.endswith("\u2026")


def test_description_within_limit():
    ev = parser.TextMessage(
        target_mode=3, text="x" * 5000, sender_clid=1, sender_name="n",
        sender_uid="u",
    )
    emb = formatter.build_payload(ev)["embeds"][0]
    assert len(emb["description"]) <= 4096


def test_title_within_limit():
    ev = parser.TextMessage(
        target_mode=3, text="hi", sender_clid=1, sender_name="n" * 300,
        sender_uid="u",
    )
    emb = formatter.build_payload(ev)["embeds"][0]
    assert len(emb["title"]) <= 256


def test_field_value_within_limit():
    ev = parser.ClientLeft(
        clid=1, nickname="n", uid="u", channel_id=1, reason_id=8,
        reason="r" * 2000,
    )
    emb = formatter.build_payload(ev)["embeds"][0]
    assert len(emb["fields"][0]["value"]) <= 1024


def test_embed_total_within_limit():
    ev = parser.ClientLeft(
        clid=1, nickname="n", uid="u", channel_id=1, reason_id=8,
        reason="r" * 5000,
    )
    emb = formatter.build_payload(ev)["embeds"][0]
    total = len(emb["title"]) + len(emb["description"]) + sum(
        len(f["name"]) + len(f["value"]) for f in emb["fields"]
    )
    assert total <= 6000


def test_many_fields_capped_at_25():
    # build_payload only emits 1 field, so exercise _payload directly
    fields = [(f"f{i}", "v") for i in range(30)]
    emb = formatter._payload("t", "d", fields)["embeds"][0]
    assert len(emb["fields"]) == 25
