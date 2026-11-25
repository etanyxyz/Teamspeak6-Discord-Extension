"""End-to-end test of the SSH query client against an in-process fake
TeamSpeak 6 query server (real SSH, real protocol, local loopback).

The fake server speaks the minimal subset of the TS6 ServerQuery protocol
verified in docs/sample-events.txt: 2-line banner, CRLF lines, responses
ending in ``error id=N msg=...``, notify events after servernotifyregister.
"""
from __future__ import annotations

import asyncio
import contextlib

import asyncssh
import pytest

from conftest import free_port, make_cfg
from tsdiscord import parser
from tsdiscord.ts_client import TSClient

WHOAMI = (
    "virtualserver_status=online virtualserver_id=1 "
    "client_id=1 client_channel_id=1 client_nickname=serveradmin "
    "client_login_name=serveradmin client_unique_identifier=serveradmin"
)

CLIENTLIST = (
    "clid=1 cid=1 client_database_id=1 client_nickname=serveradmin "
    "client_type=1 client_unique_identifier=serveradmin|"
    "clid=2 cid=1 client_database_id=2 client_nickname=Alice "
    "client_type=0 client_unique_identifier=user1"
)

NOTIFY_EVENTS = (
    # the bridge's own client entering: must NOT be forwarded
    "notifycliententerview cfid=0 ctid=1 reasonid=0 clid=1 "
    "client_unique_identifier=serveradmin client_nickname=serveradmin "
    "client_type=1\r\n"
    # a real client entering
    "notifycliententerview cfid=0 ctid=1 reasonid=0 clid=2 "
    "client_unique_identifier=user1 client_nickname=Alice client_type=0\r\n"
    # a server text message
    "notifytextmessage targetmode=3 msg=hello\\s@everyone invokerid=2 "
    "invokername=Alice invokeruid=user1\r\n"
    # a client leaving (clid only — the client must enrich from its cache)
    "notifyclientleftview cfid=1 ctid=0 reasonid=8 reasonmsg=disconnecting "
    "clid=2\r\n"
)


class FakeTSQuery(asyncssh.SSHServer):
    """Minimal TS6 ServerQuery protocol over a real SSH session."""

    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "serveradmin" and password == "secret"

    def session_requested(self):
        return self.handle

    async def handle(self, stdin, stdout, stderr):
        stdout.write("TS3\r\n")
        stdout.write(
            'Welcome to the TeamSpeak ServerQuery interface, type "help".\r\n'
        )
        await stdout.drain()
        while True:
            line = await stdin.readline()
            if not line:
                break
            cmd = line.rstrip("\r\n")
            reply = self._respond(cmd)
            if reply:
                stdout.write(reply)
                await stdout.drain()
            if cmd == "servernotifyregister event=textprivate":
                # last registration: push the event stream
                stdout.write(NOTIFY_EVENTS)
                await stdout.drain()
            if cmd == "quit":
                break

    @staticmethod
    def _respond(cmd: str) -> str:
        if cmd == "version":
            return "version=6.0.0-beta14 build=1 platform=Linux\r\nerror id=0 msg=ok\r\n"
        if cmd == "use sid=1":
            return "error id=0 msg=ok\r\n"
        if cmd == "whoami":
            return f"{WHOAMI}\r\nerror id=0 msg=ok\r\n"
        if cmd.startswith("clientupdate"):
            return "error id=0 msg=ok\r\n"
        if cmd == "channellist":
            return ("cid=1 pid=0 channel_order=0 channel_name=Default\\sChannel "
                    "total_clients=2 channel_needed_subscribe_power=0\r\n"
                    "error id=0 msg=ok\r\n")
        if cmd.startswith("clientmove"):
            return "error id=0 msg=ok\r\n"
        if cmd == "clientlist -uid":
            return f"{CLIENTLIST}\r\nerror id=0 msg=ok\r\n"
        if cmd.startswith("servernotifyregister"):
            return "error id=0 msg=ok\r\n"
        return "error id=256 msg=command\\snot\\sfound\r\n"


async def _start_fake_server() -> tuple[int, object]:
    port = free_port()
    key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        FakeTSQuery, "127.0.0.1", port, server_host_keys=[key]
    )
    return port, server


async def _drain_events(queue: asyncio.Queue, n: int) -> list:
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(queue.get(), timeout=10))
    return out


async def _stop_client(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=10)


async def test_streams_events_and_filters_self():
    port, server = await _start_fake_server()
    try:
        queue: asyncio.Queue = asyncio.Queue()
        client = TSClient(
            make_cfg(ts_ssh_port=port), queue
        )
        task = asyncio.create_task(client.run())
        try:
            events = await _drain_events(queue, 3)
        finally:
            await _stop_client(task)

        joined, msg, left = events
        assert isinstance(joined, parser.ClientJoined)
        assert joined.clid == 2
        assert joined.uid == "user1"
        assert joined.nickname == "Alice"

        assert isinstance(msg, parser.TextMessage)
        assert msg.target_mode == 3
        assert msg.text == "hello @everyone"
        assert msg.sender_uid == "user1"

        assert isinstance(left, parser.ClientLeft)
        assert left.clid == 2
        assert left.nickname == "Alice"  # enriched from the client's cache
        assert left.uid == "user1"
        assert left.reason == "disconnecting"
    finally:
        server.close()


async def test_own_client_never_forwarded():
    port, server = await _start_fake_server()
    try:
        queue: asyncio.Queue = asyncio.Queue()
        client = TSClient(make_cfg(ts_ssh_port=port), queue)
        task = asyncio.create_task(client.run())
        try:
            events = await _drain_events(queue, 3)
        finally:
            await _stop_client(task)
        # exactly 3 events: the clid=1 (own) enter was filtered out
        assert all(e.sender_clid != 1 if isinstance(e, parser.TextMessage)
                   else e.clid != 1 for e in events)
    finally:
        server.close()


async def test_wrong_password_refused():
    port, server = await _start_fake_server()
    try:
        queue: asyncio.Queue = asyncio.Queue()
        client = TSClient(
            make_cfg(ts_ssh_port=port, ts_query_password="wrong"), queue
        )
        with pytest.raises(Exception):
            await asyncio.wait_for(client.run(), timeout=10)
        assert queue.empty()
    finally:
        server.close()
