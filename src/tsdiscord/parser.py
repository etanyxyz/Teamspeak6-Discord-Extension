"""Pure parsing of TS6 ServerQuery output. No I/O.

The query protocol sends space-separated ``key=value`` records; multi-record
responses are joined with ``|``; every command response ends with an
``error id=N msg=...`` line. Text values use TS escaping (``\\s`` space,
``\\p`` tab, ``\\n`` newline, ``\\r`` CR, ``\\/`` slash, ``\\\\`` backslash).
"""
from __future__ import annotations

from dataclasses import dataclass

# --- escaping ---------------------------------------------------------------

_ESCAPES: dict[str, str] = {
    "s": " ",
    "p": "\t",
    "n": "\n",
    "r": "\r",
    "/": "/",
    "\\": "\\",
}


def unescape_ts(value: str) -> str:
    """Decode TS escapes; unknown escapes are kept verbatim."""
    out: list[str] = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append(_ESCAPES.get(nxt, "\\" + nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def escape_ts(value: str) -> str:
    """Encode a string for a TS query command parameter."""
    out: list[str] = []
    for c in value:
        if c == "\\":
            out.append("\\\\")
        elif c == "/":
            out.append("\\/")
        elif c == " ":
            out.append("\\s")
        elif c == "\t":
            out.append("\\p")
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        else:
            out.append(c)
    return "".join(out)


# --- record / key-value parsing ----------------------------------------------


def split_records(line: str) -> list[str]:
    """Split a multi-record line on ``|`` (records never contain raw pipes)."""
    return [r for r in line.split("|") if r != ""]


def parse_kv(record: str) -> dict[str, str]:
    """Parse one record into a dict. Bare keys map to ``''``."""
    d: dict[str, str] = {}
    for part in record.split(" "):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            d[k] = v
        else:
            d[part] = ""
    return d


def parse_error(line: str) -> tuple[int, str]:
    """Parse an ``error id=N msg=...`` line; msg is unescaped."""
    d = parse_kv(line)
    code = int(d.get("id", "-1"))
    return code, unescape_ts(d.get("msg", ""))


# --- event dataclasses ---------------------------------------------------------


@dataclass(frozen=True)
class ClientJoined:
    clid: int
    uid: str
    nickname: str
    channel_id: int
    client_type: int  # 1 = ServerQuery client, 0 = voice client


@dataclass(frozen=True)
class ClientLeft:
    clid: int
    nickname: str  # resolved by the client from its clid cache (may be "unknown")
    uid: str
    channel_id: int
    reason_id: int
    reason: str


@dataclass(frozen=True)
class TextMessage:
    target_mode: int  # 1=client (private), 2=channel, 3=server
    text: str
    sender_clid: int
    sender_name: str
    sender_uid: str


# --- notify event parsing ------------------------------------------------------


def parse_client_enter(line: str) -> ClientJoined | None:
    """Parse a ``notifycliententerview`` line."""
    if not line.startswith("notifycliententerview"):
        return None
    d = parse_kv(line[len("notifycliententerview"):])
    return ClientJoined(
        clid=int(d.get("clid", "0")),
        uid=d.get("client_unique_identifier", ""),
        nickname=unescape_ts(d.get("client_nickname", "")),
        channel_id=int(d.get("ctid", "0")),
        client_type=int(d.get("client_type", "0")),
    )


def parse_client_leave(line: str) -> ClientLeft | None:
    """Parse a ``notifyclientleftview`` line.

    The server only sends the clid here — name/uid are filled in by the
    client from its cache.
    """
    if not line.startswith("notifyclientleftview"):
        return None
    d = parse_kv(line[len("notifyclientleftview"):])
    return ClientLeft(
        clid=int(d.get("clid", "0")),
        nickname="",
        uid="",
        channel_id=int(d.get("cfid", "0")),
        reason_id=int(d.get("reasonid", "0")),
        reason=unescape_ts(d.get("reasonmsg", "")),
    )


def parse_text_message(line: str) -> TextMessage | None:
    """Parse a ``notifytextmessage`` line (server/channel/private)."""
    if not line.startswith("notifytextmessage"):
        return None
    d = parse_kv(line[len("notifytextmessage"):])
    return TextMessage(
        target_mode=int(d.get("targetmode", "0")),
        text=unescape_ts(d.get("msg", "")),
        sender_clid=int(d.get("invokerid", "0")),
        sender_name=unescape_ts(d.get("invokername", "")),
        sender_uid=d.get("invokeruid", ""),
    )


def parse_clientlist_record(record: str) -> tuple[int, int, str, str, int]:
    """Parse one clientlist record -> (clid, cid, nickname, uid, client_type)."""
    d = parse_kv(record)
    return (
        int(d.get("clid", "0")),
        int(d.get("cid", "0")),
        unescape_ts(d.get("client_nickname", "")),
        d.get("client_unique_identifier", ""),
        int(d.get("client_type", "0")),
    )


def parse_channellist_record(record: str) -> tuple[int, str]:
    """Parse one channellist record -> (cid, channel_name unescaped)."""
    d = parse_kv(record)
    return int(d.get("cid", "0")), unescape_ts(d.get("channel_name", ""))
