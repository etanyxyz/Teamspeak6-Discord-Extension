"""Tests for the pure TS query parser (real captured payloads)."""
from __future__ import annotations

from tsdiscord import parser


# --- escaping ---------------------------------------------------------------

def test_unescape_known_sequences():
    assert parser.unescape_ts("hello\\s\\s@everyone") == "hello  @everyone"
    assert parser.unescape_ts("a\\/b\\c") == "a/b\\c"
    # tab is \p in TS (not \t); \t is an unknown escape and kept verbatim
    assert parser.unescape_ts("line\\nbreak\\ppend") == "line\nbreak\tpend"
    assert parser.unescape_ts("line\\t") == "line\\t"
    assert parser.unescape_ts("back\\\\slash") == "back\\slash"


def test_unescape_unknown_kept_verbatim():
    assert parser.unescape_ts("a\\zb") == "a\\zb"
    assert parser.unescape_ts("trailing\\") == "trailing\\"


def test_escape_roundtrip():
    s = "nick _name* 12/34\\5\t6\n7"
    assert parser.unescape_ts(parser.escape_ts(s)) == s


def test_escape_known_sequences():
    assert parser.escape_ts("a b") == "a\\sb"
    assert parser.escape_ts("a/b") == "a\\/b"
    assert parser.escape_ts("a\\b") == "a\\\\b"


# --- record / kv parsing ------------------------------------------------------

def test_split_records():
    assert parser.split_records("a=1|b=2|") == ["a=1", "b=2"]
    assert parser.split_records("") == []


def test_parse_kv_bare_keys():
    d = parser.parse_kv("a=1 b c=3")
    assert d == {"a": "1", "b": "", "c": "3"}


def test_parse_kv_value_with_equals():
    assert parser.parse_kv("msg=a=b") == {"msg": "a=b"}


def test_parse_error():
    assert parser.parse_error("error id=0 msg=ok") == (0, "ok")
    assert parser.parse_error("error id=516 msg=invalid\\sclient\\stype") \
        == (516, "invalid client type")


# --- notify event parsing -----------------------------------------------------

ENTER_LINE = (
    "notifycliententerview cfid=0 ctid=1 reasonid=0 clid=7 "
    "client_unique_identifier=user1 client_nickname=Alice\\sDev client_type=0 "
    "client_input_muted=0 client_away=0 client_channel_group_inherited_channel_id=1"
)

LEAVE_LINE = "notifyclientleftview cfid=4 ctid=0 reasonid=8 reasonmsg=disconnecting clid=10"

SERVER_MSG_LINE = (
    "notifytextmessage targetmode=3 msg=hello\\sserver\\s@everyone "
    "invokerid=7 invokername=Alice\\sDev invokeruid=user1"
)

CHANNEL_MSG_LINE = (
    "notifytextmessage targetmode=2 msg=hi\\schannel invokerid=7 "
    "invokername=Alice\\sDev invokeruid=user1"
)


def test_parse_client_enter():
    ev = parser.parse_client_enter(ENTER_LINE)
    assert ev is not None
    assert ev.clid == 7
    assert ev.uid == "user1"
    assert ev.nickname == "Alice Dev"
    assert ev.channel_id == 1
    assert ev.client_type == 0


def test_parse_client_enter_wrong_line_is_none():
    assert parser.parse_client_enter(LEAVE_LINE) is None
    assert parser.parse_client_enter(SERVER_MSG_LINE) is None
    assert parser.parse_client_enter("clientlist") is None


def test_parse_client_leave():
    ev = parser.parse_client_leave(LEAVE_LINE)
    assert ev is not None
    assert ev.clid == 10
    assert ev.channel_id == 4
    assert ev.reason_id == 8
    assert ev.reason == "disconnecting"
    assert ev.nickname == ""  # filled in from the client's cache
    assert ev.uid == ""


def test_parse_client_leave_wrong_line_is_none():
    assert parser.parse_client_leave(ENTER_LINE) is None


def test_parse_text_message_server():
    ev = parser.parse_text_message(SERVER_MSG_LINE)
    assert ev is not None
    assert ev.target_mode == 3
    assert ev.text == "hello server @everyone"
    assert ev.sender_clid == 7
    assert ev.sender_name == "Alice Dev"
    assert ev.sender_uid == "user1"


def test_parse_text_message_channel():
    ev = parser.parse_text_message(CHANNEL_MSG_LINE)
    assert ev is not None
    assert ev.target_mode == 2
    assert ev.text == "hi channel"


def test_parse_text_message_wrong_line_is_none():
    assert parser.parse_text_message(ENTER_LINE) is None
    assert parser.parse_text_message("error id=0 msg=ok") is None


def test_parse_clientlist_record():
    rec = ("clid=2 cid=1 client_database_id=2 client_nickname=Alice "
           "client_type=0 client_unique_identifier=user1")
    clid, cid, nickname, uid, ctype = parser.parse_clientlist_record(rec)
    assert (clid, cid, nickname, uid, ctype) == (2, 1, "Alice", "user1", 0)


def test_parse_channellist_record():
    rec = "cid=2 pid=0 channel_order=0 channel_name=Test\\sCh total_clients=0"
    cid, name = parser.parse_channellist_record(rec)
    assert (cid, name) == (2, "Test Ch")
