# NOTES — TeamSpeak 6 → Discord Bridge

Working log. Re-read this first if you lose track.

## Phase 0 — Recon (done)

- Host: Linux Mint 22.3 (Zena), x86_64, kernel 7.0.0-43.
- Docker 29.10.0, Docker Compose v5.8.0, Python 3.12.3 (/usr/bin/python3).
- Free ports confirmed: UDP 9987, TCP 10011, 10022, 10080, 30033 (all free).
- Repo https://github.com/etanyxyz/Teamspeak6-Discord-Extension — empty except a
  one-line README. Cloned into the workspace; git identity etanyxyz /
  etanyxyz@users.noreply.github.com; remote switched to SSH
  (git@github.com:etanyxyz/Teamspeak6-Discord-Extension.git) — SSH auth verified OK.
- `sudo apt` needs a password (no passwordless sudo). Workaround used: created venv
  with `python3 -m venv --without-pip` + get-pip.py. python3.12-venv not installed;
  if apt becomes needed, ask user for a password or pre-install packages.
- `.env` already existed in the workspace with key `discord webhook = <url>`;
  normalized to `DISCORD_WEBHOOK_URL=<url>` in place. Value kept secret, never printed.
- Skeleton created: src/tsdiscord/{__init__,config,ts_client,parser,discord,formatter,main}.py,
  tests/, docs/, scripts/, .gitignore, .env.example, pyproject.toml, .venv (gitignored).
- Deps installed in .venv: pip 26.3 (aiohttp/dotenv/pytest to be installed as needed).

## Phase 1 — TS6 server in Docker (done)

- Image: `teamspeaksystems/teamspeak6-server:6.0.0-beta14` (official;
  verified via teamspeak6.com Docker guide + github.com/teamspeak/teamspeak6-server).
  `docker-compose.dev.yml` pins this tag.
- First start: license accepted via `TSSERVER_LICENSE_ACCEPTED=accept`;
  server log printed initial credentials (query login password, WebQuery API key,
  serveradmin token). Captured into `.env` (gitignored). Data persisted in
  named volume `ts6-data` (compose project name: teamspeak6-discord-extension).
- Ports (confirmed in server log): 9987/udp voice, 30033/tcp file transfer,
  10022/tcp **SSH query**, 10080/tcp HTTP WebQuery, 10443/tcp HTTPS query.
  **No raw TCP ServerQuery** (TS6 dropped the TS3-style 10011 interface).
- Network: shared bridge network `ts6-net` so the bridge container reaches the
  server as `ts6`.
- `query_ip_allowlist` default is 127.0.0.1/32 + ::1/128 — from the sandbox
  host the queries arrive as 127.0.0.1 (OK); from a bridge container on
  ts6-net the source IP is the container IP → allowlist must be widened
  (TSSERVER_QUERY_IP_ALLOWLIST) if the bridge runs in its own container.
  **Decision: for dev, run the bridge in the same compose project with
  allowlist `0.0.0.0/0` is too broad — use the ts6-net subnet.** Check subnet
  before finalizing compose.

## Phase 2 — query API learned empirically (done)

Probe scripts (throwaway, NOT committed): `scripts/probe.py`,
`scripts/scenario.py`, `scripts/scenario2.py`, `scripts/scenario3.py`.
Raw payloads + visibility matrix: `docs/sample-events.txt`.

Transport decision: **SSH ServerQuery (port 10022) via asyncssh**.
WebQuery (10080) is stateless (`GET /{command}?params`, header `x-api-key`);
`servernotifyregister` is out of api-key scope and there is no event stream,
so WebQuery cannot deliver events.

SSH query protocol (TS6 beta14):
- Connect with password auth (serveradmin account), open a **shell** channel
  (no PTY). asyncssh: `ssh.create_process(None, stdin=PIPE, stdout=PIPE)`.
- Banner = 2 lines (`TS3` + `Welcome to the TeamSpeak ServerQuery interface...`);
  lines are CRLF and each line is prefixed with `\r`.
- Send one command per line; each response ends with `error id=N msg=...`.
  Multi-record lines use `|`. Commands before `use sid=1` fail with
  `error id=1024 invalid serverID`.
- `whoami` after `use` gives the query client's own `client_id` (clid) —
  the bridge uses it to never forward itself.
- Events after `servernotifyregister event=server|textserver|textchannel|textprivate`:
  `notifycliententerview` (full record incl. uid/nickname),
  `notifyclientleftview` (**clid only** → cache needed),
  `notifytextmessage targetmode=3|2|1` with `invokerid/invokername/invokeruid`,
  **no server-side timestamp** (bridge adds its own).
- No `ping` command → keepalive = periodic `version`.

VISIBILITY (the key risk, empirically settled — full matrix in
docs/sample-events.txt):
- enter/leave events: server-wide.
- server text: server-wide.
- **channel text: ONLY the channel the query client is in** (registration
  `event=textchannel id={foreign cid}` is accepted but delivers nothing;
  group already has view power 100 — scoping is by design in beta14).
- private text: NOT testable — TS6 rejects private messages sent BY query
  clients (`error id=516 invalid client type`); no voice client exists in the
  sandbox. Whether a query client receives privates addressed TO it is unknown.

CONSEQUENCE FOR THE BRIDGE (document in README, not hidden):
- Full coverage: joins/leaves + server chat everywhere.
- Channel chat: mirrored for exactly ONE channel — the one the bridge's
  query client sits in (default: server's default channel). Optional config
  `TS_BRIDGE_CHANNEL` (channel name) lets the user pick a different single
  channel.
- Private messages: `FORWARD_PRIVATE_MESSAGES=false` default; only privates
  where the bridge's own query client is the recipient could possibly be
  captured (unverified); privates between other users are NOT visible.
- Flood protection: connect/auth bursts lock the source IP ~10 min
  (in-memory). Bridge: single persistent connection, keepalive every ~60 s,
  reconnect with exponential backoff (cap 60 s) after drops.

## Phase 3 — build the bridge (done)

- Transport: SSH ServerQuery on 10022 via `asyncssh` (see Phase 2).
  `ssh.create_process(None, stdin=PIPE, stdout=PIPE)`, no PTY; banner
  (2 lines) drained at connect.
- `ts_client.py`: single persistent connection. Setup sequence:
  `use sid=1` → `whoami` (own clid — its own enter/leave are never forwarded)
  → `clientupdate client_nickname=<BRIDGE_NICKNAME>` → optional
  `channellist` + `clientmove` to `TS_BRIDGE_CHANNEL` (name match)
  → seed clid→(nickname, uid) cache from `clientlist -uid` (multi-record
  `|` lines) → `servernotifyregister` ×4 (server, textserver, textchannel,
  textprivate). Reader task routes `notify*` lines to the event queue,
  everything else to the response queue; `command()` holds a lock and
  raises `TSQueryError` on nonzero error id, `TSQueryClosed` on EOF.
  Keepalive = `version` every 60 s (no `ping` command in TS6).
- `parser.py`: pure functions only. TS escapes (`\s \p \n \/ \\\ ...`),
  `|` record splitting, kv parsing, `error id=N` parsing, and the three
  event parsers (`ClientJoined`, `ClientLeft`, `TextMessage` dataclasses).
- `formatter.py`: pure → webhook payload. All safety in one place:
  `safe_text()` neutralizes @everyone/@here/role-IDs with ZWSP, escapes
  markdown, truncates. Discord limits: content 2000, description 4096,
  title/field-name 256, field value 1024, 25 fields, 6000-char total
  embed budget (description trimmed to fit the budget).
- `discord.py`: worker on the event queue; 429 → honor `Retry-After`
  header (seconds) or JSON `retry_after` (ms); 5xx/ClientError → retry
  with backoff 2 s doubling, cap 60 s; other non-2xx → log (no URL) +
  drop; malformed events → log + skip, never crash.
- `main.py`: wiring + SIGTERM/SIGINT via `loop.add_signal_handler`;
  reconnect loop wraps `client.run()`, backoff 1 s → 60 s cap.
- `config.py`: env-driven; `load_dotenv(".env", override=False)` with
  explicit path (no directory walk) so test isolation and container env
  both work. Fields per plan: TS_HOST, TS_SSH_PORT (10022),
  TS_QUERY_USER/PASSWORD, TS_QUERY_API_KEY (aux), DISCORD_WEBHOOK_URL,
  BRIDGE_NICKNAME (TS6-Discord-Bridge), TS_BRIDGE_CHANNEL,
  FORWARD_PRIVATE_MESSAGES (false), IGNORE_CLIENT_UIDS, LOG_LEVEL.
- Entry point: console script `tsdiscord` (`tsdiscord.main:main`).

## Phase 4 — testing (done)

- 56 tests, all green (`pytest -q`), ~3 s.
- `test_parser.py`: real captured payloads + escapes/`|`/kv/error parsing.
- `test_formatter.py`: payload shape, mention neutralization (ZWSP),
  markdown escaping, every Discord limit incl. 1024 field value and the
  6000-char embed budget, `allowed_mentions` always empty.
- `test_config.py`: monkeypatch.chdir(tmp_path) + delenv of every TS var
  so the repo's .env cannot leak; missing/invalid values raise ConfigError.
- `test_discord.py`: local aiohttp mock webhook — 204, 429 via header and
  via JSON body, 5xx-then-204 (retried), 404 dropped, run() drains queue,
  run() survives unformattable events, URL never appears in logs.
- `test_ts_client.py`: end-to-end against an in-process fake TS6 query
  server speaking the verified protocol over REAL SSH (asyncssh 2.24.1
  server API: `create_server` + `SSHServer` subclass, `session_requested`
  returns a `handle(stdin, stdout, stderr)` callable). Asserts setup
  sequence, event stream, own-client filtering (clid from `whoami`),
  leave-event enrichment from the clid cache, wrong-password refusal.
- Known test gotcha: `stdout.write()` in the fake server must receive ONE
  concatenated string, not a tuple (a tuple silently kills the session).

## Open questions

- (Phase 5) widen `query_ip_allowlist` (TSSERVER_QUERY_IP_ALLOWLIST) to
  the ts6-net subnet before running the bridge as a container.

## Phase status

- [x] Phase 0: Recon + skeleton
- [x] Phase 1: TS6 server in Docker
- [x] Phase 2: Learn query API empirically
- [x] Phase 3: Build the bridge
- [x] Phase 4: Testing
- [ ] Phase 5: Integration test vs live server
- [ ] Phase 6: Packaging and docs
