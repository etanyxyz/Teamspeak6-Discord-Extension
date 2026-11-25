"""Async SSH ServerQuery client for TeamSpeak 6.

Connects to the TS6 server over SSH (password auth, shell channel, no PTY),
selects the virtual server, registers for notify events and streams parsed
events (join/leave/text) into an ``asyncio.Queue``. Keeps a clid ->
(nickname, uid) cache because leave events only carry the clid.

Protocol notes (verified on 6.0.0-beta14, see docs/sample-events.txt):
banner is 2 lines; response lines are CRLF and prefixed with ``\\r``;
each command response ends with ``error id=N msg=...``; there is no
``ping`` command, so keepalive sends ``version``.
"""
from __future__ import annotations

import asyncio
import logging

import asyncssh

from . import parser
from .config import BridgeConfig

log = logging.getLogger("tsdiscord.ts_client")

KEEPALIVE_INTERVAL = 60.0
COMMAND_TIMEOUT = 10.0
EVENTS = ("server", "textserver", "textchannel", "textprivate")


class TSQueryError(RuntimeError):
    """The server answered a command with an error."""


class TSQueryClosed(RuntimeError):
    """The query stream went away (server restart, network drop, flood lock)."""


class TSClient:
    def __init__(self, cfg: BridgeConfig, out_queue: asyncio.Queue) -> None:
        self._cfg = cfg
        self._queue = out_queue
        self._ssh: asyncssh.SSHClientConnection | None = None
        self._proc: asyncssh.SSHClientProcess | None = None
        self._resp: asyncio.Queue[str] = asyncio.Queue()
        self._cmd_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self._own_clid: int | None = None
        self._cache: dict[int, tuple[str, str]] = {}  # clid -> (nickname, uid)
        self.channel_name: str = "server"

    # -- lifecycle ------------------------------------------------------------

    async def run(self) -> None:
        """Connect, set up, stream events until the connection dies.

        Raises nothing on stream death; the caller (main) decides to
        reconnect with backoff.
        """
        self._closed.clear()
        self._resp = asyncio.Queue()
        await self.connect()
        reader = asyncio.create_task(self._read_loop())
        try:
            await self.setup()
            keep = asyncio.create_task(self._keepalive())
            await self._closed.wait()
            keep.cancel()
        finally:
            reader.cancel()
            await self.close()

    async def connect(self) -> None:
        log.info("connecting to TS query at %s:%s", self._cfg.ts_host, self._cfg.ts_ssh_port)
        self._ssh = await asyncssh.connect(
            self._cfg.ts_host,
            port=self._cfg.ts_ssh_port,
            username=self._cfg.ts_query_user,
            password=self._cfg.ts_query_password,
            known_hosts=None,
        )
        # Shell channel (no PTY, no exec command) is what TS6 expects.
        self._proc = await self._ssh.create_process(
            None, stdin=asyncssh.PIPE, stdout=asyncssh.PIPE
        )
        # Drain the 2-line banner before sending any command.
        for _ in range(2):
            await asyncio.wait_for(self._proc.stdout.readline(), timeout=15.0)
        log.info("query session open")

    async def setup(self) -> None:
        await self.command("use sid=1")
        who = parser.parse_kv(" ".join(await self.command("whoami")))
        self._own_clid = int(who.get("client_id", "0") or 0)
        if self._cfg.bridge_nickname:
            await self.command(
                "clientupdate client_nickname=" + parser.escape_ts(self._cfg.bridge_nickname)
            )
        if self._cfg.ts_bridge_channel:
            wanted = self._cfg.ts_bridge_channel
            for rec in await self.command("channellist"):
                cid, name = parser.parse_channellist_record(rec)
                if name == wanted:
                    await self.command(f"clientmove clid=0 cid={cid}")
                    self.channel_name = wanted
                    break
            else:
                raise TSQueryError(f"channel {wanted!r} not found on the server")
        else:
            self.channel_name = "default channel"
        # Seed the clid cache (leave events only carry the clid).
        for line in await self.command("clientlist -uid"):
            for rec in parser.split_records(line):
                clid, _cid, nickname, uid, _t = parser.parse_clientlist_record(rec)
                self._cache[clid] = (nickname, uid)
        for ev in EVENTS:
            await self.command(f"servernotifyregister event={ev}")
        log.info(
            "query ready: own clid=%s, channel=%r, cached clients=%d",
            self._own_clid, self.channel_name, len(self._cache),
        )

    async def close(self) -> None:
        try:
            if self._proc is not None and self._proc.stdin is not None:
                self._proc.stdin.write("quit\n")
                await self._proc.stdin.drain()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
        try:
            if self._ssh is not None:
                await self._ssh.close()
        except Exception:  # noqa: BLE001
            pass
        self._ssh = None
        self._proc = None

    # -- command/response plumbing ----------------------------------------------

    async def command(self, cmd: str, timeout: float = COMMAND_TIMEOUT) -> list[str]:
        """Send one command, return its non-error response lines."""
        if self._proc is None or self._proc.stdin is None:
            raise TSQueryClosed("query session is not open")
        async with self._cmd_lock:
            while not self._resp.empty():
                self._resp.get_nowait()  # drop stale lines
            self._proc.stdin.write(cmd + "\n")
            await self._proc.stdin.drain()
            lines: list[str] = []
            while True:
                try:
                    line = await asyncio.wait_for(self._resp.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    raise TSQueryClosed(f"no response to {cmd!r}") from None
                if line.startswith("error "):
                    code, msg = parser.parse_error(line)
                    if code != 0:
                        raise TSQueryError(f"{cmd!r} -> error {code}: {msg}")
                    break
                lines.append(line)
            return lines

    async def _read_loop(self) -> None:
        assert self._proc is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                log.warning("query stream closed by server")
                self._closed.set()
                return
            line = line.rstrip("\r\n").lstrip("\r").strip()
            if not line or line == "TS3" or line.startswith("Welcome"):
                continue
            if line.startswith("notify"):
                await self._on_notify(line)
            else:
                await self._resp.put(line)

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            try:
                await self.command("version", timeout=15.0)
            except Exception as e:  # noqa: BLE001 - any failure means "dead"
                log.warning("keepalive failed: %s", e)
                self._closed.set()
                return

    # -- event filtering & publishing --------------------------------------------

    def _forward(self, uid: str, clid: int) -> bool:
        if clid is not None and self._own_clid is not None and clid == self._own_clid:
            return False
        return uid not in self._cfg.ignore_client_uids

    async def _on_notify(self, line: str) -> None:
        joined = parser.parse_client_enter(line)
        if joined is not None:
            self._cache[joined.clid] = (joined.nickname, joined.uid)
            if self._forward(joined.uid, joined.clid):
                await self._queue.put(joined)
            return

        left = parser.parse_client_leave(line)
        if left is not None:
            nickname, uid = self._cache.pop(left.clid, ("unknown", ""))
            if self._forward(uid, left.clid):
                resolved = parser.ClientLeft(
                    clid=left.clid,
                    nickname=nickname,
                    uid=uid,
                    channel_id=left.channel_id,
                    reason_id=left.reason_id,
                    reason=left.reason,
                )
                await self._queue.put(resolved)
            return

        msg = parser.parse_text_message(line)
        if msg is not None:
            if msg.target_mode == 1 and not self._cfg.forward_private_messages:
                return
            if self._forward(msg.sender_uid, msg.sender_clid):
                await self._queue.put(msg)
